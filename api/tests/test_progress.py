"""What the reader sees while an answer is being made.

The rules that matter: the refusal word never reaches the screen, a draft
that is thrown away is never seen, text arrives in order and bundled, and the
final answer is always the last thing sent.
"""
import asyncio

from api.agents import answer_agent
from api.agents.progress import Progress, ProgressPublisher, RefusalGate


class Recorder(Progress):
    def __init__(self):
        self.events = []

    def stage(self, name, **info):
        self.events.append(("stage", name, info))

    def text(self, piece):
        self.events.append(("text", piece))

    def reset(self):
        self.events.append(("reset",))

    @property
    def shown(self):
        out = ""
        for e in self.events:
            if e[0] == "text":
                out += e[1]
            elif e[0] == "reset":
                out = ""
        return out


def feed(gate, *pieces):
    for p in pieces:
        gate.text(p)


def test_the_refusal_word_never_reaches_the_screen():
    r = Recorder()
    feed(RefusalGate(r), "INS", "UFFI", "CIENT")
    assert r.shown == ""


def test_an_answer_that_starts_like_the_refusal_word_still_shows():
    r = Recorder()
    feed(RefusalGate(r), "In", "surance must be ", "carried [Segment 2].")
    assert r.shown == "Insurance must be carried [Segment 2]."


def test_a_bolded_refusal_is_still_a_refusal():
    r = Recorder()
    feed(RefusalGate(r), "**INSUFFICIENT**")
    assert r.shown == ""


async def test_the_publisher_bundles_text_and_keeps_order():
    sent = []

    async def publish(payload):
        sent.append(payload)

    p = ProgressPublisher(publish, history_id=7, interval=0.02).start()
    p.stage("searching")
    for piece in ["The ", "answer ", "is"]:
        p.text(piece)
    await asyncio.sleep(0.05)
    p.reset()
    p.text("Replaced")
    await p.close()

    events = [e for payload in sent for e in payload["events"]]
    assert all(payload["history_id"] == 7 for payload in sent)
    assert [e["kind"] for e in events] == ["stage", "text", "reset", "text"]
    assert events[1]["text"] == "The answer is"
    assert events[3]["text"] == "Replaced"


async def test_nothing_is_sent_after_close():
    sent = []

    async def publish(payload):
        sent.append(payload)

    p = ProgressPublisher(publish, history_id=1, interval=0.01).start()
    await p.close()
    p.text("late")
    await asyncio.sleep(0.03)
    assert all(not payload["events"] for payload in sent) or sent == []


# --- through the graph ------------------------------------------------------

from api.tests.test_answer_agent import FakeComposer, FakeStore, agents, stub_models, turn  # noqa: E402,F401


async def _ask(progress, **kw):
    agent = answer_agent.AnswerAgent(store=FakeStore(), composer=FakeComposer())
    return await agent.run(user_id=1, message="vacation and refunds?", language="English",
                           comprehension_level="beginner", workspace_id=1, progress=progress, **kw)


async def test_each_search_the_model_makes_is_shown_as_it_happens(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation accrual"})),
        turn(("search_document", {"document_id": 1, "query": "carry over"})),
        turn(("Findings", {"passage_ids": ["c1"]})),
    ]
    stub_models["checking whether each claim"] = "1: SUPPORTED"
    r = Recorder()
    await _ask(r)
    stages = [(e[1], e[2]) for e in r.events if e[0] == "stage"]
    assert stages == [
        ("searching", {}),
        ("searching", {"query": "vacation accrual"}),
        ("reading", {"document": "handbook.pdf", "query": "carry over"}),
        ("writing", {}),
        ("checking", {"claims": 1}),
    ]
    assert r.shown.startswith("Vacation accrues")
    kinds = [e[1] if e[0] == "stage" else e[0] for e in r.events]
    assert kinds.index("writing") < kinds.index("text") < kinds.index("checking")


async def test_multi_path_shows_only_the_combined_answer(agents, stub_models):
    agents["coordinator"] = [
        turn(("search", {"query": "vacation refunds"})),
        turn(("Findings", {"delegate": [{"document_id": 1, "find": "vacation"},
                                        {"document_id": 2, "find": "refunds"}]})),
    ]
    agents["worker"] = [turn(("Passages", {"passage_ids": ["c1", "c2"]}))]
    stub_models["Write ONE answer"] = "Vacation accrues monthly [Segment 1]. Refunds in writing [Segment 2]."
    stub_models["checking whether each claim"] = "1: SUPPORTED\n2: SUPPORTED"
    r = Recorder()
    await _ask(r)
    # The workers' own drafts are not streamed; only the writer's answer is.
    assert r.shown == "Vacation accrues monthly [Segment 1]. Refunds in writing [Segment 2]."
