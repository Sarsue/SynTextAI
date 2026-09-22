"""The verifier checks what a citation claims, not only that it is well formed.

The model is stubbed: these test what the verifier does with a verdict, which
is deterministic. Whether the model's verdicts are right is a measurement, not
a unit test, and lives in api/evals/verifier_check.py.
"""
import pytest

from api.agents import verifier
from api.services.answer_composer import UNVERIFIED_MARK, Draft, AnswerComposer

SEGMENTS = [
    {"file_name": "handbook.pdf", "page_number": 3, "content": "Vacation accrues at 1.25 days per month of service.", "file_url": "https://storage.googleapis.com/b/handbook.pdf"},
    {"file_name": "handbook.pdf", "page_number": 9, "content": "Refunds are issued within 30 days of a written request.", "file_url": "https://storage.googleapis.com/b/handbook.pdf"},
    {"file_name": "policy.pdf", "page_number": 2, "content": "The office opens at 8 am on weekdays.", "file_url": "https://storage.googleapis.com/b/policy.pdf"},
]


def _draft(text):
    _, targets = AnswerComposer()._format_context_and_sources(SEGMENTS)
    return Draft("answer", text, SEGMENTS, targets)


def _stub(monkeypatch, *replies):
    calls = []
    queue = list(replies)

    async def fake(prompt, **kwargs):
        calls.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr(verifier.llm_service, "gradient_chat", fake)
    return calls


def test_claims_are_the_cited_sentences():
    text = (
        "Vacation accrues at 1.25 days a month [Segment 1]. "
        "Refunds take 30 days. [Segment 2]\n"
        "- Background. The office opens at 8 am [Segment 3]\n"
        "No citation here."
    )
    claims = verifier.split_claims(text)
    assert [c.segments for c in claims] == [[1], [2], [3]]
    assert claims[0].text == "Vacation accrues at 1.25 days a month."
    # A decimal is not a sentence end.
    assert "1.25" in claims[0].text
    # An uncited sentence before a cited one on the same line travels with it.
    assert claims[2].text.startswith("Background.")


def test_combined_markers_are_one_claim_citing_both():
    claims = verifier.split_claims("Both apply [Segment 1, 2].")
    assert len(claims) == 1 and claims[0].segments == [1, 2]


async def test_supported_claims_are_left_alone(monkeypatch):
    calls = _stub(monkeypatch, "1: SUPPORTED\n2: SUPPORTED")
    text = "Vacation accrues at 1.25 days a month [Segment 1]. Refunds take 30 days [Segment 2]."
    out, report = await verifier.verify(_draft(text))
    assert out.text == text
    assert report.status == "ok" and report.supported == 2 and report.recited == 0
    assert len(calls) == 1
    # Each passage appears once, whatever cites it.
    assert calls[0].count("[Passage 1,") == 1


async def test_a_wrong_pointer_is_moved_to_the_passage_that_holds_it(monkeypatch):
    _stub(monkeypatch, "1: NOT SUPPORTED", "1: 2")
    out, report = await verifier.verify(_draft("Refunds are issued within 30 days [Segment 1]."))
    assert out.text == "Refunds are issued within 30 days [Segment 2]."
    assert report.recited == 1 and report.unverified == 0


async def test_a_claim_nothing_supports_keeps_its_words_and_loses_its_link(monkeypatch):
    _stub(monkeypatch, "1: NOT SUPPORTED", "1: NONE")
    out, report = await verifier.verify(_draft("Refunds take 90 days [Segment 2]. Next sentence."))
    assert "[Segment" not in out.text
    assert "Refunds take 90 days." + UNVERIFIED_MARK in out.text
    assert report.unverified == 1

    rendered = AnswerComposer().render(out)
    assert "could not confirm" in rendered
    assert "**Sources:**" not in rendered


async def test_a_pick_outside_the_candidates_is_ignored(monkeypatch):
    # The model may name a passage it was not offered. It gets no say there.
    _stub(monkeypatch, "1: NOT SUPPORTED", "1: 3")
    out, report = await verifier.verify(_draft("Refunds are issued within 30 days [Segment 2]."))
    assert report.recited == 0 and report.unverified == 1


@pytest.mark.parametrize("reply", ["", "I think these are mostly fine."])
async def test_an_unusable_reply_never_changes_the_answer(monkeypatch, reply):
    _stub(monkeypatch, reply)
    text = "Refunds take 30 days [Segment 2]."
    out, report = await verifier.verify(_draft(text))
    assert out.text == text
    assert report.status == "error" and report.unchecked == 1


async def test_an_exception_never_changes_the_answer(monkeypatch):
    async def boom(prompt, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(verifier.llm_service, "gradient_chat", boom)
    text = "Refunds take 30 days [Segment 2]."
    out, report = await verifier.verify(_draft(text))
    assert out.text == text and report.status == "error"


async def test_a_claim_the_reply_skips_is_unchecked_not_failed(monkeypatch):
    _stub(monkeypatch, "1: SUPPORTED")
    text = "Vacation accrues monthly [Segment 1]. Refunds take 30 days [Segment 2]."
    out, report = await verifier.verify(_draft(text))
    assert out.text == text
    assert report.supported == 1 and report.unchecked == 1


async def test_only_answers_are_verified(monkeypatch):
    calls = _stub(monkeypatch)
    for d in (Draft.final("No answer."), Draft("uncited", "Something.", SEGMENTS, {})):
        out, report = await verifier.verify(d)
        assert out is d and report.status == "skipped"
    assert calls == []


def test_render_links_moved_citations_and_drops_out_of_range_ones():
    rendered = AnswerComposer().render(_draft("A [Segment 2]. B [Segment 9]."))
    assert "[[1]](https://storage.googleapis.com/b/handbook.pdf#page=9)" in rendered
    assert "Segment" not in rendered


def test_a_segment_named_in_prose_does_not_reach_the_reader():
    rendered = AnswerComposer().render(_draft(
        "Limits are listed in Table 3 (see Segment 10). Refunds take 30 days [Segment 2]. "
        "Our segment 2 customers are dentists [Segment 1]."
    ))
    assert "Segment 10" not in rendered and "Table 3." in rendered
    # A customer's own words about segments stay.
    assert "segment 2 customers" in rendered


def test_a_line_of_bare_markers_cites_the_text_above_it():
    claims = verifier.split_claims("E4 means the cooling system is abnormal.\n\n[Segment 1] [Segment 2]")
    assert len(claims) == 1
    assert claims[0].segments == [1, 2]
    assert claims[0].text.startswith("E4 means")
