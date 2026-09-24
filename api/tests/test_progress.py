"""What the reader sees while an answer is being made.

The rules that matter: the refusal word never reaches the screen, a draft
that is thrown away is never seen, text arrives in order and bundled, and the
final answer is always the last thing sent.
"""
import asyncio

from api.agents import answer_agent, coordinator
from api.agents.progress import Deferred, Progress, ProgressPublisher, RefusalGate


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


def test_a_discarded_draft_is_never_seen():
    r = Recorder()
    d = Deferred()
    d.text("a draft nobody should see")
    d.discard()
    d.text(" more")
    assert r.events == []


def test_a_released_draft_shows_what_it_held_then_goes_live():
    r = Recorder()
    d = Deferred()
    d.text("held ")
    d.release(r)
    d.text("live")
    assert r.shown == "held live"


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

from api.tests.test_answer_agent import FakeComposer, FakeStore, stub_models  # noqa: E402,F401


async def _ask(progress, **kw):
    agent = answer_agent.AnswerAgent(store=FakeStore(), composer=FakeComposer())
    return await agent.run(user_id=1, message="vacation and refunds?", language="English",
                           comprehension_level="beginner", workspace_id=1, progress=progress, **kw)


async def test_single_path_reports_searching_writing_then_checking(stub_models):
    stub_models["decide which documents"] = "1: vacation"
    stub_models["checking whether each claim"] = "1: SUPPORTED"
    r = Recorder()
    await _ask(r)
    stages = [e[1] for e in r.events if e[0] == "stage"]
    assert stages == ["searching", "writing", "checking"]
    assert r.shown.startswith("Vacation accrues")
    # The draft's text arrives after "writing" and before "checking".
    kinds = [e[1] if e[0] == "stage" else e[0] for e in r.events]
    assert kinds.index("writing") < kinds.index("text") < kinds.index("checking")


async def test_multi_path_shows_the_combined_answer_never_the_discarded_draft(stub_models):
    stub_models["decide which documents"] = "1: vacation\n2: refunds"
    stub_models["Write ONE answer"] = "Vacation accrues monthly [Segment 1]. Refunds in writing [Segment 2]."
    stub_models["checking whether each claim"] = "1: SUPPORTED\n2: SUPPORTED"
    r = Recorder()
    await _ask(r)
    stages = [(e[1], e[2]) for e in r.events if e[0] == "stage"]
    assert [s for s, _ in stages] == ["searching", "reading", "writing", "checking"]
    assert stages[1][1] == {"documents": 2}
    # Only the writer's answer was shown. The single-document draft, which
    # contains both documents' first passages, was not.
    assert r.shown == "Vacation accrues monthly [Segment 1]. Refunds in writing [Segment 2]."
