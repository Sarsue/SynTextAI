"""Finding a page by the one token that identifies it.

THE BUG THIS FIXES

The text arm of hybrid_search ranks with `ts_rank_cd`, which scores cover
density -- how tightly the query's words cluster in a chunk -- and has no
inverse document frequency at all. It is not BM25, whatever the code called it.
BM25's defining property is that a rare term counts for more than a common one,
and Postgres full-text ranking has no mechanism for that.

Measured 2026-08-14 on the HVAC corpus, 1,528 chunks, for the question
"liquid pressure is 251 psig and subcooling is 10 degrees":

    251            3 chunks      <- the discriminator
    degrees        9
    subcooling    29
    psig          49
    liquid       123
    pressure     196
    temperature  215

All weighted the same. Eight chunks thick with "liquid pressure temperature"
outranked the one chunk in the corpus containing 251, and not one of those eight
contained it. The target sat at rank 16 of 25 and never reached the model.

Every failing benchmark question had this shape: 4350 in 1 chunk, 670 in 1,
E4 in 2, F8 in 3, 335 in 3, 1600 in 5.

With the literal arm, the same target moves to rank 3, and the benchmark goes
from 8/20 answers and 9/20 citations to 9/20 and 11/20, deterministically.

WHAT IS ASSERTED

Not rank positions, which move with any tuning. The properties that make the
arm worth having:

  - a question's identifying tokens are recognised, and ordinary quantities are
    not mistaken for them
  - a chunk holding a rare token outranks chunks that merely share common words
  - a question with no identifying tokens behaves exactly as before
"""
import uuid

import pytest
import pytest_asyncio

from api.repositories.async_file_repository import literal_tokens

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
class TestWhichTokensIdentify:
    """literal_tokens is pure, so these need no database and no event loop."""

    def test_a_pressure_reading_is_an_identifier(self):
        assert "251" in literal_tokens(
            "liquid pressure is 251 psig and subcooling is 10 degrees"
        )

    def test_an_error_code_is_an_identifier(self):
        assert "e4" in literal_tokens("what does error code E4 mean")
        assert "f8" in literal_tokens("what is fault F8 on the mini split")

    def test_a_small_quantity_is_not_an_identifier(self):
        """"10 degrees" is a quantity. It appears all over a technical corpus,
        so treating it as an address would drag in everything."""
        assert "10" not in literal_tokens("subcooling is 10 degrees")
        assert "6" not in literal_tokens("6 degree subcooling")

    def test_a_model_size_is_an_identifier(self):
        assert "48" in literal_tokens("fan CFM for the 48")
        assert "4350" in literal_tokens("is the CFM 4350")

    def test_a_fraction_survives_whole(self):
        """1/12 is a motor horsepower, and "1" and "12" separately are noise."""
        assert "1/12" in literal_tokens("PSC fan motor horsepower 1/12")

    def test_prose_has_no_identifiers(self):
        assert literal_tokens("what does a run capacitor actually do") == []
        assert literal_tokens("") == []


@pytest_asyncio.fixture(loop_scope="session")
async def corpus(store, tenant):
    """One chunk holding the rare token, buried under eight that are not.

    The decoys are what the real corpus looks like: pages thick with the
    question's ordinary vocabulary, and vectors CLOSER to the query than the
    target's. That is the situation that defeated the old ranking, where the
    real target sat at rank 16 of 25 behind fifteen chunks from other manuals.

    Without both halves this test cannot fail. A two-document fixture with equal
    embeddings lets the target win on its own, which is how the first version of
    this file passed with the feature switched off.
    """
    from api.models.orm_models import Chunk, Segment

    workspace_id = await tenant.workspace("Literal retrieval")

    async def add(name, page, content, vector):
        file_id = await store.file_repo.add_file(
            user_id=tenant.owner, file_name=name, file_url="", workspace_id=workspace_id,
        )
        async with store.file_repo.get_async_session() as session:
            seg = Segment(file_id=file_id, page_number=page, content=content)
            session.add(seg)
            await session.commit()
            segment_id = seg.id
            session.add(Chunk(
                file_id=file_id, segment_id=segment_id, content=content,
                embedding=vector, content_hash="h-" + uuid.uuid4().hex[:8],
            ))
            await session.commit()
        return file_id

    # The query vector these are ranked against.
    QUERY_VEC = [0.05] * DIM

    # Far from the query, and short: only the token saves it.
    target = await add(
        f"target-{uuid.uuid4().hex[:6]}.pdf", 9,
        "| 251 | 78 | 76 | 74 | 72 |", [0.5] + [0.01] * (DIM - 1),
    )

    decoys = []
    for i in range(8):
        decoys.append(await add(
            f"decoy{i}-{uuid.uuid4().hex[:6]}.pdf", 20 + i,
            "Liquid pressure and liquid line temperature. Measure the liquid "
            "pressure, then read liquid line temperature against subcooling in "
            "degrees. Liquid pressure rises as liquid line temperature rises, "
            f"and subcooling degrees follow the liquid pressure. Section {i}.",
            QUERY_VEC,
        ))
    return {"workspace_id": workspace_id, "target": target, "decoys": decoys,
            "query_vec": QUERY_VEC}


async def test_the_chunk_holding_the_rare_token_wins(store, tenant, corpus):
    """The bug, put back.

    Before the literal arm the decoy won, because it repeats every common word
    in the question and the ranker had no notion that 251 is rarer than "liquid".
    """
    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="liquid pressure is 251 psig and subcooling is 10 degrees, "
              "what should the liquid line temperature be",
        query_embedding=corpus["query_vec"],
        workspace_id=corpus["workspace_id"],
    )
    assert hits, "retrieval returned nothing"
    ranks = {h["file_id"]: i for i, h in enumerate(hits)}
    assert corpus["target"] in ranks, "the chunk containing 251 was not retrieved"
    best_decoy = min((ranks[d] for d in corpus["decoys"] if d in ranks), default=10**6)
    assert ranks[corpus["target"]] < best_decoy, (
        f"the chunk holding 251 ranked {ranks[corpus['target']]}, behind a decoy "
        f"at {best_decoy}: common words beat the identifying token"
    )


async def test_a_question_with_no_identifiers_still_works(store, tenant, corpus):
    """The arm must switch itself off cleanly, not match everything or nothing.

    An empty tsquery is a runtime error in Postgres, so this also guards the
    placeholder that stands in when there are no tokens.
    """
    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="how should liquid line temperature be measured",
        query_embedding=corpus["query_vec"],
        workspace_id=corpus["workspace_id"],
    )
    assert hits, "a question with no identifying tokens returned nothing at all"


# ---------------------------------------------------------------------------
# The guardrail, and the pages it must not arbitrate
# ---------------------------------------------------------------------------


class TestWhichPagesTheTextLayerCanVerify:
    """A vision read is checked against the text layer. That only works if the
    text layer holds everything on the page.

    Measured 2026-08-14 on page 7 of goodman_gszc7_service.pdf, a leader-line
    nomenclature diagram with 202 vector drawings: the vision model read it
    correctly at 3,462 characters, and sixteen of its numbers were absent from
    the text layer, so the whole page was rejected and its garbled text kept.

        '21'  (cabinet width, benchmark Q14)   in text layer: FALSE
        '410' (refrigerant,   benchmark Q15)   in text layer: FALSE

    Those labels are drawn, not written. The check was rejecting precisely the
    pages vision exists for, and it cost four of twenty benchmark questions;
    fixing it took the suite from 9/20 to 13/20 answers and 11/20 to 15/20
    citations.
    """

    def _page(self, *, drawings: int):
        class _FakePage:
            rect = type("R", (), {"width": 612.0, "height": 792.0})()

            def get_drawings(self):
                return [{}] * drawings

            def get_images(self, full=False):
                return []

            def get_image_bbox(self, img):
                return None

        return _FakePage()

    def test_a_figure_page_cannot_arbitrate(self):
        from api.processors.pdf_processor import PDFProcessor

        proc = PDFProcessor.__new__(PDFProcessor)
        assert proc._text_layer_is_credible(self._page(drawings=202)) is False

    def test_an_ordinary_page_still_arbitrates(self):
        """The strict reject must survive where its assumption holds, or a model
        inventing a torque value goes unnoticed."""
        from api.processors.pdf_processor import PDFProcessor

        proc = PDFProcessor.__new__(PDFProcessor)
        assert proc._text_layer_is_credible(self._page(drawings=25)) is True

    def test_a_page_that_cannot_be_inspected_keeps_the_strict_check(self):
        """Failing open here would silently disable the guardrail everywhere."""
        from api.processors.pdf_processor import PDFProcessor

        class _Broken:
            def get_drawings(self):
                raise RuntimeError("damaged page")

        proc = PDFProcessor.__new__(PDFProcessor)
        assert proc._text_layer_is_credible(_Broken()) is True
