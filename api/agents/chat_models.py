"""The chat model the agents think with: LangChain's ChatOpenAI.

The agents that decide things (the researcher and the document workers) call
tools, and tool calling, structured output, retries and the agent loop are
LangChain's, not ours. DeepInfra speaks the OpenAI protocol, so ChatOpenAI is
pointed at the same endpoint and key as llm_service.

Checked 2026-09-28 with one real call: gpt-oss-20b on DeepInfra called a
search tool, read the result, then called the finishing tool with the right
passage id, and every response carried `usage.estimated_cost`.

Two things are carried over from llm_service because they were measured there:
temperature 0 (the same question must find the same pages) and a token budget
that leaves room for the model's reasoning, which on gpt-oss comes out of the
same allowance as the reply.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult

from api.services import llm_service

# Enough for a tool call or a list of passage ids plus the thinking before it.
AGENT_MAX_TOKENS = 2000

# One model call. The whole search has its own deadline (coordinator.py).
CALL_TIMEOUT = 60.0


class _Cost(AsyncCallbackHandler):
    """Adds what the provider charged to the question's cost ledger.

    Async, so it runs in the agent's own task and sees the ledger the
    question opened (a ContextVar in llm_service).
    """

    def __init__(self, model: str):
        self.model = model

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        llm_service.record_usage(self.model, dict(usage))


logger = logging.getLogger(__name__)


def require_tool_call(decision_tool: str, attempts: int = 2):
    """Middleware: a turn with no tool call is asked again, not taken as the end.

    create_agent ends its loop on the first turn without a tool call, and
    asks the provider to prevent that (tool_choice "any", because a
    ToolStrategy decision is required). DeepInfra ignores it for gpt-oss.
    Checked 2026-09-28: told to call a tool, it replied "Hello! How can I help
    you today?". On the first real runs this, not the question, decided most
    outcomes: the researcher's last turn came back empty (no tool call, no
    text, its whole budget spent thinking, the failure llm_service has always
    retried for) and the loop ended with no decision, so the answer was
    written from everything it had read rather than from its choice.

    So the missing turn is asked for again, with a reminder that the only way
    to finish is the decision tool.
    """
    from langchain.agents.middleware import AgentMiddleware, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    reminder = HumanMessage(
        f"Reply only by calling a tool. If you have searched enough, call {decision_tool} "
        "with your decision now."
    )

    def _turn(response: Any) -> Optional[AIMessage]:
        if isinstance(response, AIMessage):
            return response
        if isinstance(response, ModelResponse):
            return next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
        return None

    class RequireToolCall(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            response = await handler(request)
            for attempt in range(attempts):
                turn = _turn(response)
                if turn is None or turn.tool_calls:
                    return response
                logger.info({"event": "agent.turn_without_tool_call", "attempt": attempt + 1,
                             "had_text": bool(turn.content)})
                response = await handler(request.override(messages=[*request.messages, reminder]))
            return response

    return RequireToolCall()


async def run_agent(agent: Any, inputs: Dict[str, Any], deadline: float) -> Tuple[Dict[str, Any], bool]:
    """Run a create_agent graph, and keep what it had found if time runs out.

    Streams the graph's state rather than awaiting the final one, so a search
    cut off by the deadline still hands back every passage it had read.
    Returns (last state, whether the deadline stopped it).
    """
    last: Dict[str, Any] = {}

    async def go() -> None:
        nonlocal last
        async for state in agent.astream(inputs, stream_mode="values"):
            last = state

    try:
        await asyncio.wait_for(go(), timeout=deadline)
        return last, False
    except asyncio.TimeoutError:
        logger.warning({"event": "agent.deadline", "agent": getattr(agent, "name", None), "seconds": deadline})
        return last, True


def _reasons(model: str) -> bool:
    return "gpt-oss" in model or "thinking" in model.lower()


def chat_model(model: str, effort: Optional[str] = None, max_tokens: int = AGENT_MAX_TOKENS) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=llm_service.INFERENCE_BASE_URL,
        api_key=llm_service.MODEL_ACCESS_KEY or "unset",
        temperature=llm_service.TEMPERATURE,
        # Only reasoning models take this; an instruct model (the coordinator's
        # Qwen) has no thinking to bound.
        reasoning_effort=(effort or None) if _reasons(model) else None,
        max_tokens=max(max_tokens, llm_service.MIN_COMPLETION_TOKENS),
        timeout=CALL_TIMEOUT,
        max_retries=2,
        callbacks=[_Cost(model)],
    )
