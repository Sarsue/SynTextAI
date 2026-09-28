"""The coordinator, the document workers and the writer, with every model scripted.

What is tested is the wiring: that the model's own searches are what gets
retrieved, that its choice of passages is what the answer is written from,
that nothing it names can reach outside the reader's documents, and that
citations survive being combined. Whether the model searches WELL is not a
unit test's question.

The agent model is a ScriptedModel: it returns one scripted turn per call,
tool calls included, so the real create_agent loop, ToolStrategy and the
call-limit middleware all run exactly as they do in production.
"""
from typing import Any, Callable, Dict, List
import itertools

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult

from api.agents import answer_agent, chat_models, coordinator, writer
from api.agents.document_worker import WorkerResult
from api.services import llm_service
from api.services.answer_composer import Draft, AnswerComposer


def _chunk(chunk_id, file_id, name, page, content):
    return {"chunk_id": chunk_id, "file_id": file_id, "file_name": name, "page_number": page,
            "content": content, "segment_id": file_id * 1000 + page,
            "file_url": f"https://storage.googleapis.com/b/{name}", "meta_data": {}}


HANDBOOK = [_chunk(1, 1, "handbook.pdf", 3, "Vacation accrues at 1.25 days per month.")]
POLICY = [_chunk(2, 2, "policy.pdf", 7, "Refund requests must be made in writing.")]


class FakeRepo:
    def __init__(self):
        self.calls: List[Any] = []

    async def hybrid_search(self, *, file_id=None, query=None, **kw):
        self.calls.append((file_id, query))
        if file_id == 1:
            return HANDBOOK
        if file_id == 2:
            return POLICY
        if file_id is not None:
            return []
        return HANDBOOK + POLICY

    async def get_files_for_user(self, user_id, **kw):
        return {"items": [{"id": 1, "file_name": "handbook.pdf"}, {"id": 2, "file_name": "policy.pdf"}]}

    async def read_page_for(self, *, file_id, page_number, **kw):
        if file_id == 1 and page_number == 3:
            return {"segment_id": 1003, "file_id": 1, "page_number": 3, "file_name": "handbook.pdf",
                    "file_url": "https://storage.googleapis.com/b/handbook.pdf", "meta_data": {},
                    "content": "Vacation accrues at 1.25 days per month, up to 15 days a year."}
        return None


class FakeStore:
    def __init__(self):
        self.file_repo = FakeRepo()

        class W:
            async def accessible_workspace_ids(self, user_id):
                return [1]

        self.workspace_repo = W()


class FakeComposer(AnswerComposer):
    """Composes from whatever it is given, citing segment 1."""

    def __init__(self):
        self.seen = []

    async def compose(self, query, history, chunks, language, level, model=None, sink=None):
        self.seen.append([c["file_id"] for c in chunks])
        if not chunks:
            return Draft.final("nothing")
        _, targets = self._format_context_and_sources(chunks)
        text = f"{chunks[0]['content']} [Segment 1]"
        if sink is not None:
            sink.text(text)
        return Draft("answer", text, chunks, targets)


_ids = itertools.count()


def turn(*calls):
    """One model turn: tool calls as (name, args) pairs."""
    return AIMessage(content="", tool_calls=[
        {"name": n, "args": a, "id": f"call{next(_ids)}", "type": "tool_call"} for n, a in calls
    ])


class ScriptedModel(BaseChatModel):
    """Plays back one turn per call. `script(role, n)` gives turn n."""

    script: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        role = "worker" if "ONE document" in system else "coordinator"
        if role == "worker":
            role = "worker:" + system.split('"')[1]
        n = sum(isinstance(m, AIMessage) for m in messages)
        return ChatResult(generations=[ChatGeneration(message=self.script(role, n))])


@pytest.fixture
def agents(monkeypatch):
    """Scripts per role: agents["coordinator"] = [turn, turn, ...]."""
    scripts: Dict[str, List[AIMessage]] = {}

    def play(role, n):
        turns = scripts.get(role) or scripts.get(role.split(":")[0]) or []
        return turns[min(n, len(turns) - 1)] if turns else AIMessage(content="")

    monkeypatch.setattr(chat_models, "chat_model", lambda *a, **k: ScriptedModel(script=play))

    async def fake_embed(text):
        return [0.0]

    monkeypatch.setattr("api.agents.tools.get_text_embedding", fake_embed)
    return scripts


@pytest.fixture
def stub_models(monkeypatch):
    """The single calls: the writer and the verifier."""
    replies = {}

    async def fake_chat(prompt, **kw):
        for key, reply in replies.items():
            if key in prompt:
                return reply
        return ""

    async def fake_stream(prompt, sink, **kw):
        text = await fake_chat(prompt, **kw)
        if text:
            sink.text(text)
        return text

    monkeypatch.setattr(llm_service, "gradient_chat", fake_chat)
    monkeypatch.setattr(llm_service, "stream_chat", fake_stream)
    return replies


async def _ask(store, composer, **kw):
    agent = answer_agent.AnswerAgent(store=store, composer=composer)
    return await agent.run(user_id=1, message="vacation and refunds?", language="English",
                           comprehension_level="beginner", workspace_id=1, **kw)


async def test_the_answer_is_written_from_the_passages_the_model_chose(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation accrual"})),
        turn(("Findings", {"passage_ids": ["c1"]})),
    ]
    stub_models["checking whether each claim"] = "1: SUPPORTED"
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)

    assert out["plan"] == "answer" and out["mode"] == "single" and out["workers"] == []
    # The model's query is the one that was searched.
    assert store.file_repo.calls == [(None, "vacation accrual")]
    assert out["searches"] == ["vacation accrual"]
    # The search returned both documents; the model judged only one answers.
    assert composer.seen == [[1]]
    assert out["verification"]["supported"] == 1


async def test_it_searches_again_when_the_first_search_does_not_answer(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "time off"})),
        turn(("search_document", {"document_id": 2, "query": "refund in writing"})),
        turn(("Findings", {"passage_ids": ["c2"]})),
    ]
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)
    assert store.file_repo.calls == [(None, "time off"), (2, "refund in writing")]
    assert out["searches"] == ["time off", "refund in writing"]
    assert composer.seen == [[2]]


async def test_ids_the_model_never_read_are_ignored(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation"})),
        turn(("Findings", {"passage_ids": ["c999", "c1", "c1"]})),
    ]
    composer = FakeComposer()
    await _ask(FakeStore(), composer)
    assert composer.seen == [[1]]


async def test_a_whole_page_can_be_read_and_cited(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation"})),
        turn(("read_page", {"document_id": 1, "page_number": 3})),
        turn(("Findings", {"passage_ids": ["p1003"]})),
    ]
    stub_models["checking whether each claim"] = "1: SUPPORTED"
    out = await _ask(FakeStore(), FakeComposer())
    assert "up to 15 days" in out["response"]
    assert "handbook.pdf#page=3" in out["response"]


async def test_when_the_model_judges_nothing_answers_that_stands(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "parking"})),
        turn(("Findings", {"passage_ids": []})),
    ]
    composer = FakeComposer()
    out = await _ask(FakeStore(), composer)
    assert out["plan"] == "nothing"
    assert composer.seen == [[]]


async def test_a_model_that_never_decides_is_stopped_and_answered_from_what_it_read(agents, stub_models):
    agents["coordinator"] = [turn(("search", {"query": "vacation"}))]  # forever
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)
    assert out["plan"] == "gathered"
    # The tool limit held: no more searches ran than it allows.
    assert len(store.file_repo.calls) <= coordinator.MAX_TOOL_CALLS
    assert composer.seen and sorted(composer.seen[0]) == [1, 2]


async def test_two_documents_get_a_worker_each_and_one_combined_answer(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation refunds"})),
        turn(("Findings", {"delegate": [{"document_id": 1, "find": "vacation accrual"},
                                        {"document_id": 2, "find": "refund rules"}]})),
    ]
    agents["worker:handbook.pdf"] = [
        turn(("search_document", {"document_id": 1, "query": "accrual rate"})),
        turn(("Passages", {"passage_ids": ["c1"]})),
    ]
    agents["worker:policy.pdf"] = [
        # Asks for another document; its tools stay inside its own.
        turn(("search_document", {"document_id": 1, "query": "refunds"})),
        turn(("Passages", {"passage_ids": ["c2"]})),
    ]
    stub_models["Write ONE answer"] = (
        "Vacation accrues monthly [Segment 1]. Refunds must be in writing [Segment 2]."
    )
    stub_models["checking whether each claim"] = "1: SUPPORTED\n2: SUPPORTED"
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)

    assert out["plan"] == "delegate" and out["mode"] == "multi"
    assert sorted(w["file_id"] for w in out["workers"]) == [1, 2]
    assert sorted(c for c in store.file_repo.calls if c[0]) == [(1, "accrual rate"), (2, "refunds")]
    assert sorted(composer.seen) == [[1], [2]]
    assert "policy.pdf#page=7" in out["response"]
    assert "handbook.pdf#page=3" in out["response"]
    assert out["verification"]["supported"] == 2
    assert set(out["searches"]) == {"vacation refunds", "accrual rate", "refunds"}


async def test_a_document_outside_the_readers_scope_gets_no_worker(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation"})),
        turn(("Findings", {"passage_ids": ["c1"],
                           "delegate": [{"document_id": 7, "find": "secrets"}]})),
    ]
    composer = FakeComposer()
    out = await _ask(FakeStore(), composer)
    assert out["workers"] == [] and composer.seen == [[1]]


async def test_a_question_about_one_file_searches_only_that_file(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "anything"})),
        turn(("search_document", {"document_id": 2, "query": "other file"})),
        turn(("Findings", {"passage_ids": ["c1"]})),
    ]
    store = FakeStore()
    await _ask(store, FakeComposer(), file_id=1)
    assert [c[0] for c in store.file_repo.calls] == [1, 1]


async def test_agent_calls_are_added_to_the_cost_ledger():
    cost = chat_models._Cost("openai/gpt-oss-20b")
    with llm_service.cost_ledger() as ledger:
        await cost.on_llm_end(LLMResult(generations=[], llm_output={"token_usage": {
            "prompt_tokens": 161, "completion_tokens": 40, "estimated_cost": 1.043e-05}}))
    row = ledger["openai/gpt-oss-20b"]
    assert row["calls"] == 1 and row["input_tokens"] == 161 and row["cost_usd"] == 1.043e-05


# --- the writer ---------------------------------------------------------------

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


# --- the guards around the model ------------------------------------------------

async def test_a_turn_with_no_tool_call_is_asked_again(monkeypatch, stub_models):
    """DeepInfra ignores "a tool call is required" for gpt-oss, and create_agent
    ends on the first turn without one. The middleware asks again instead."""

    def play(role, n, messages):
        if "Reply only by calling a tool" in str(messages[-1].content):
            return turn(("Findings", {"passage_ids": ["c1"]}))
        if n == 0:
            return turn(("search", {"query": "vacation"}))
        return AIMessage(content="")  # thought, and said nothing

    class Replaying(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            n = sum(isinstance(m, AIMessage) for m in messages)
            return ChatResult(generations=[ChatGeneration(message=play("coordinator", n, messages))])

    monkeypatch.setattr(chat_models, "chat_model", lambda *a, **k: Replaying())

    async def fake_embed(text):
        return [0.0]

    monkeypatch.setattr("api.agents.tools.get_text_embedding", fake_embed)
    composer = FakeComposer()
    out = await _ask(FakeStore(), composer)
    assert out["plan"] == "answer" and composer.seen == [[1]]


async def test_an_identical_search_is_not_run_twice(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "Vacation  accrual"})),
        turn(("search", {"query": "vacation accrual"})),
        turn(("Findings", {"passage_ids": ["c1"]})),
    ]
    store = FakeStore()
    await _ask(store, FakeComposer())
    assert store.file_repo.calls == [(None, "Vacation  accrual")]


def test_a_passage_already_read_comes_back_as_its_id():
    from api.agents.tools import _listing
    seen: set = set()
    first = _listing(HANDBOOK + POLICY, "none", seen)
    again = _listing(HANDBOOK, "none", seen)
    assert "Vacation accrues" in first and "Refund requests" in first
    assert "Vacation accrues" not in again and "[c1]" in again and "Nothing new" in again


async def test_when_the_search_budget_is_spent_only_the_decision_is_left(monkeypatch, stub_models):
    """A model that keeps asking for searches past the limit is left with
    nothing to call but the decision."""
    offered = []

    class Greedy(ScriptedModel):
        tools: Any = None

        def bind_tools(self, tools, **kwargs):
            return Greedy(tools=[getattr(t, "name", None) or t.get("function", {}).get("name") for t in tools])

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            offered.append(self.tools)
            if self.tools == ["Findings"]:
                return ChatResult(generations=[ChatGeneration(message=turn(("Findings", {"passage_ids": ["c1"]})))])
            q = f"q{sum(isinstance(m, AIMessage) for m in messages)}"
            return ChatResult(generations=[ChatGeneration(message=turn(("search", {"query": q})))])

    monkeypatch.setattr(chat_models, "chat_model", lambda *a, **k: Greedy())

    async def fake_embed(text):
        return [0.0]

    monkeypatch.setattr("api.agents.tools.get_text_embedding", fake_embed)
    store, composer = FakeStore(), FakeComposer()
    out = await _ask(store, composer)
    assert out["plan"] == "answer" and composer.seen == [[1]]
    assert len(store.file_repo.calls) == coordinator.MAX_TOOL_CALLS
    assert offered[-1] == ["Findings"]
