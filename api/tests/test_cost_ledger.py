"""What a question cost is added up from what the provider reports.

The provider is stubbed at the HTTP layer, so this exercises the real path:
_post_json records usage into whatever ledger is open, including across the
parallel tasks LangGraph starts and including responses a caller rejects.
"""
import asyncio

from api.services import llm_service


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, bodies):
        self.bodies = list(bodies)

    async def post(self, url, headers=None, json=None):
        return FakeResponse(self.bodies.pop(0))


def _body(cost, content="ok", prompt=100, completion=10):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "estimated_cost": cost},
    }


def _use(monkeypatch, *bodies):
    client = FakeClient(bodies)

    async def get_client():
        return client

    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm_service, "get_client", get_client)
    monkeypatch.setattr(llm_service.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(llm_service, "MODEL_ACCESS_KEY", "test")


async def test_every_call_in_a_question_is_counted_by_model(monkeypatch):
    _use(monkeypatch, _body(0.002), _body(0.00004), _body(0.00004))
    with llm_service.cost_ledger() as ledger:
        await llm_service.gradient_chat("a", model="anthropic/claude-sonnet-5")
        # Parallel tasks see and add to the same ledger.
        await asyncio.gather(
            llm_service.gradient_chat("b", model="openai/gpt-oss-20b"),
            llm_service.gradient_chat("c", model="openai/gpt-oss-20b"),
        )
    summary = llm_service.cost_summary(ledger)
    assert summary["cost_usd"] == 0.00208
    assert summary["by_model"]["openai/gpt-oss-20b"]["calls"] == 2
    assert summary["by_model"]["anthropic/claude-sonnet-5"]["input_tokens"] == 100


async def test_a_rejected_response_was_still_paid_for(monkeypatch):
    # The first reply is empty, so gradient_chat retries. Both were billed.
    _use(monkeypatch, _body(0.001, content=""), _body(0.001))
    with llm_service.cost_ledger() as ledger:
        assert await llm_service.gradient_chat("a") == "ok"
    assert llm_service.cost_summary(ledger)["cost_usd"] == 0.002


async def test_nothing_is_recorded_without_an_open_ledger(monkeypatch):
    _use(monkeypatch, _body(0.5))
    await llm_service.gradient_chat("a")
    with llm_service.cost_ledger() as ledger:
        pass
    assert ledger == {}
