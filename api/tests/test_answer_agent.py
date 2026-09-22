"""The coordinator, the document workers and the writer, with every model stubbed.

What is tested is the wiring: which path a question takes, what each worker is
allowed to see, and that citations survive being combined. Whether the models
make good choices is the benchmark's job.
"""
import pytest

from api.agents import answer_agent, coordinator, verifier, writer
from api.agents.document_worker import WorkerResult
from api.services.answer_composer import Draft, AnswerComposer


def _chunk(file_id, name, page, content, score=1.0):
    return {"file_id": file_id, "file_name": name, "page_number": page, "content": content,
            "segment_id": file_id * 1000 + page, "file_url": f"https://storage.googleapis.com/b/{name}",
            "similarity_score": score}


HANDBOOK = [_chunk(1, "handbook.pdf", 3, "Vacation accrues at 1.25 days per month.", 0.9)]
POLICY = [_chunk(2, "policy.pdf", 7, "Refund requests must be made in writing.", 0.5)]


class FakeRepo:
    def __init__(self):
        self.calls = []

    async def hybrid_search(self, *, file_id=None, **kw):
        self.calls.append(file_id)
        if file_id == 1:
            return HANDBOOK
        if file_id == 2:
            return POLICY
        return HANDBOOK + POLICY


class FakeStore:
    def __init__(self):
        self.file_repo = FakeRepo()

        class W:
            async def accessible_workspace_ids(self, user_id):
                return [1]

        self.workspace_repo = W()


class FakeComposer(AnswerComposer):
    """compose answers from whatever it is given, citing segment 1."""

    def __init__(self):
        self.seen = []

    async def compose(self, query, history, chunks, language, level, model=None):
        self.seen.append([c["file_id"] for c in chunks])
        if not chunks:
            return Draft.final("nothing")
        _, targets = self._format_context_and_sources(chunks)
        return Draft("answer", f"{chunks[0]['content']} [Segment 1]", chunks, targets)


@pytest.fixture
def stub_models(monkeypatch):
    replies = {}

    async def fake_chat(prompt, **kw):
        for key, reply in replies.items():
            if key in prompt:
                return reply
        return ""

    async def fake_embed(text):
        return [0.0]

    async def fake_process(message, history):
        return message, []

    monkeypatch.setattr(coordinator.llm_service, "gradient_chat", fake_chat)
    monkeypatch.setattr(answer_agent, "get_text_embedding", fake_embed)
    monkeypatch.setattr("api.agents.document_worker.get_text_embedding", fake_embed)
    monkeypatch.setattr(answer_agent.query_processor, "process", fake_process)
    return replies


async def _ask(store, composer, **kw):
    agent = answer_agent.AnswerAgent(store=store, composer=composer)
    return await agent.run(user_id=1, message="vacation and refunds?", language="English",
                           comprehension_level="beginner", workspace_id=1, **kw)


async def test_one_document_takes_the_single_path(stub_models):
    stub_models["decide which documents"] = "1: vacation"
    stub_models["checking whether each claim"] = "1: SUPPORTED"
    composer = FakeComposer()
    out = await _ask(FakeStore(), composer)
    assert out["mode"] == "single" and out["workers"] == []
    # The single path reads the broad evidence, both documents, as before.
    assert composer.seen == [[1, 2]]
    assert out["verification"]["supported"] == 1


async def test_two_documents_get_a_worker_each_and_one_combined_answer(stub_models):
    stub_models["decide which documents"] = "1: vacation accrual\n2: refund rules"
    stub_models["Write ONE answer"] = (
        "Vacation accrues monthly [Segment 1]. Refunds must be in writing [Segment 2]."
    )
    stub_models["checking whether each claim"] = "1: SUPPORTED\n2: SUPPORTED"
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)

    assert out["mode"] == "multi"
    assert sorted(w["file_id"] for w in out["workers"]) == [1, 2]
    # Each worker searched and read its own document only.
    assert sorted(c for c in store.file_repo.calls if c) == [1, 2]
    assert sorted(composer.seen) == [[1], [2]]
    # The second document's citation resolves to the second document's page.
    assert "policy.pdf#page=7" in out["response"]
    assert "handbook.pdf#page=3" in out["response"]
    assert out["verification"]["supported"] == 2


async def test_a_question_about_one_file_never_asks_the_coordinator(stub_models):
    stub_models["decide which documents"] = "1: x\n2: y"
    out = await _ask(FakeStore(), FakeComposer(), file_id=1)
    assert out["plan"] == "scoped" and out["mode"] == "single"


async def test_an_unreadable_plan_falls_back_to_the_single_path(stub_models):
    stub_models["decide which documents"] = "Both look relevant to me."
    out = await _ask(FakeStore(), FakeComposer())
    assert out["plan"] == "unreadable" and out["mode"] == "single"


def test_markers_are_renumbered_into_one_list():
    a = WorkerResult(1, "a.pdf", "", Draft("answer", "X [Segment 1]. Y [Segment 2].", HANDBOOK * 2))
    b = WorkerResult(2, "b.pdf", "", Draft("answer", "Z [Segment 1, 2].", POLICY * 2))
    segments, shifted = writer.merge([a, b])
    assert len(segments) == 4
    assert shifted[1][1] == "Z [Segment 3, 4]."


async def test_a_writer_that_drops_every_citation_is_not_used(monkeypatch):
    async def uncited(prompt, **kw):
        return "A smooth answer with no markers at all."

    monkeypatch.setattr(writer.llm_service, "gradient_chat", uncited)
    a = WorkerResult(1, "a.pdf", "", Draft("answer", "X [Segment 1].", HANDBOOK))
    b = WorkerResult(2, "b.pdf", "", Draft("answer", "Z [Segment 1].", POLICY))
    draft = await writer.write("q", [a, b])
    assert "**a.pdf**" in draft.text and "Z [Segment 2]." in draft.text


async def test_documents_that_do_not_answer_are_left_out(monkeypatch):
    calls = []

    async def never(prompt, **kw):
        calls.append(prompt)
        return ""

    monkeypatch.setattr(writer.llm_service, "gradient_chat", never)
    a = WorkerResult(1, "a.pdf", "", Draft.final("INSUFFICIENT"))
    b = WorkerResult(2, "b.pdf", "", Draft("answer", "Z [Segment 1].", POLICY))
    draft = await writer.write("q", [a, b])
    # One document answered, so there was nothing to combine and no call.
    assert draft.text == "Z [Segment 1]." and calls == []


def test_candidates_rank_documents_by_their_evidence():
    docs = coordinator.candidates(HANDBOOK + POLICY + POLICY)
    assert [d["file_id"] for d in docs] == [2, 1]
