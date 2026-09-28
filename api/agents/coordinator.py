"""The coordinator: an agent that searches until it has found the answer's
passages, then decides who writes the answer.

    search ─► read ─► judge ─► search again ... ─► decide
                                                    ├─ these passages answer it
                                                    └─ these documents each need a worker

The model controls retrieval: it writes every query, reads what comes back,
judges whether it answers, and searches again, inside one document or across
all of them, until it does. Code only supplies the tools (tools.py), keeps them
inside what the reader may see, and sets the limits below.

Built from LangChain's parts, not ours: create_agent is the tool loop,
ToolStrategy is the decision at the end (the model finishes by calling a tool
whose arguments are the Findings below), and the call-limit middleware is the
hard stop. This is the "agentic RAG" pattern in LangChain's docs, with the
retrieve-then-delegate step of their deep-agent RAG example.

Decided with Osas 2026-09-28: the model, not code, controls retrieval and
judges what it retrieved. The fixed pipeline this replaced (one search of 25,
a ranking of documents by score, a model choosing among the top six) is in
git history; its known weakness was that a document the first search missed
could never be chosen.

WHAT IT NEVER DOES

It never fails a question. A decision it cannot read, a limit reached or the
deadline passed all fall back to every passage it had found, which the
answer step then selects from.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from api.agents import chat_models
from api.agents.models import COORDINATOR_EFFORT, COORDINATOR_MODEL
from api.agents.progress import NO_PROGRESS, Progress
from api.agents.tools import Scope, found_passages, research_tools, searches_made

logger = logging.getLogger(__name__)

# Most workers one question may start.
MAX_DOCUMENTS = 4

# Most passages the answer is written from when the model chose them.
MAX_PASSAGES = 12

# Tool calls per question: several rounds of searching and reading.
# Was 8; lowered for speed (2026-09-28). Searches made in one turn run
# together, so a budget of 5 is not five rounds unless the model wants it.
MAX_TOOL_CALLS = int(os.getenv("RESEARCH_MAX_TOOL_CALLS", "5"))
# Model calls, always more than tool calls. With the two equal, a model that
# made one tool call a turn spent its last turn searching and had none left to
# decide, which is what the first real runs did (2026-09-28): eight searches,
# no decision, and an answer written from everything instead of its choice.
MAX_MODEL_CALLS = MAX_TOOL_CALLS + 2

# Wall clock for the whole search. The provider's latency has a long tail;
# past this, the answer is written from what was found.
DEADLINE = float(os.getenv("RESEARCH_DEADLINE", "30"))


class Delegation(BaseModel):
    document_id: int = Field(description="The document_id to send a worker to.")
    find: str = Field(description="What to find in that document, in a few words.")


class Findings(BaseModel):
    """Your decision, once you have searched enough."""

    passage_ids: List[str] = Field(
        default_factory=list,
        description=("Ids of the passages that answer the question, most important "
                     "first, for example [\"c812\", \"p77\"]. Only passages you read "
                     "that actually state the answer."),
    )
    delegate: List[Delegation] = Field(
        default_factory=list,
        description=("Only when the answer needs several documents, each read in "
                     "depth: one entry per document. Otherwise empty."),
    )


SYSTEM_PROMPT = f"""You find the passages in the user's documents that answer their question. You do not write the answer: another step writes it from the passages you choose.

How to work:
- Search with your own queries. Phrase them the way the document would say it, not as a question.
- Be fast: make all the searches you already know you need in ONE turn, as several tool calls at once. A question with several parts, or about several documents, gets a search for each in that same turn.
- Judge what comes back. A passage counts only if it actually states what the question asks. When nothing answers, search again with different words, search inside the most likely document with search_document, or read_page when a passage is cut off or a table continues.
- Use list_documents when the question names a document or you need to know what exists.
- Every passage you have read stays available by its id, so never repeat a search. A search that finds nothing new means: search for something different, or decide.
- Stop as soon as you have what the answer needs. You have at most {MAX_TOOL_CALLS} tool calls; after that you must decide.

Then give your decision:
- passage_ids: the passages that answer, most important first.
- delegate: only when the question needs several documents each read in depth: it compares documents, combines facts that live in different documents, or several documents each give their own answer to it. One entry per document with what to find there, at most {MAX_DOCUMENTS}. When you delegate, passage_ids may be empty.
- If the documents do not answer the question, return empty lists."""


@dataclass
class Assignment:
    file_id: int
    file_name: str
    focus: str


@dataclass
class Plan:
    # The passages the answer is written from, in the model's order.
    passages: List[Dict[str, Any]] = field(default_factory=list)
    # One per document worker. Empty on the single path.
    assignments: List[Assignment] = field(default_factory=list)
    # answer | delegate | gathered | nothing | deadline | error
    reason: str = "answer"
    # The queries the model chose, for the run record.
    searches: List[str] = field(default_factory=list)
    tool_calls: int = 0
    # Everything any search returned, by passage id.
    found: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def is_multi(self) -> bool:
        return bool(self.assignments)


def _question(message: str, history: str) -> str:
    if not history:
        return message
    return f"Conversation so far:\n{history}\n\nQuestion: {message}"


def _tool_calls(messages: List[Any]) -> int:
    return sum(len(getattr(m, "tool_calls", None) or []) for m in messages)


def _agent(scope: Scope, progress: Progress):
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from langchain.agents.structured_output import ToolStrategy

    return create_agent(
        chat_models.chat_model(COORDINATOR_MODEL, COORDINATOR_EFFORT),
        research_tools(scope, progress),
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(Findings),
        middleware=[
            # Past the tool limit further searches are refused and the model
            # is told so, which leaves it the turn it needs to decide.
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
            chat_models.require_tool_call("Findings"),
        ],
        name="coordinator",
    )


async def plan(scope: Scope, message: str, history: str = "", progress: Progress = NO_PROGRESS) -> Plan:
    progress.stage("searching")
    try:
        state, timed_out = await chat_models.run_agent(
            _agent(scope, progress),
            {"messages": [{"role": "user", "content": _question(message, history)}]},
            DEADLINE,
        )
    except Exception as e:
        logger.warning({"event": "coordinator.failed", "error": str(e)[:300]})
        return Plan(reason="error")

    messages = state.get("messages") or []
    found = found_passages(messages)
    decision: Optional[Findings] = state.get("structured_response")
    result = Plan(searches=searches_made(messages), tool_calls=_tool_calls(messages), found=found)

    if decision is None:
        # Cut off, or never decided: answer from everything it read.
        result.passages = list(found.values())
        result.reason = "deadline" if timed_out else ("gathered" if found else "nothing")
    else:
        chosen = [found[i] for i in dict.fromkeys(decision.passage_ids) if i in found][:MAX_PASSAGES]
        assignments = await _assignments(scope, decision.delegate, found)
        if len(assignments) >= 2 or (assignments and not chosen):
            result.assignments = assignments
            result.reason = "delegate"
        elif chosen:
            result.passages = chosen
            result.reason = "answer"
        else:
            # It read and judged that nothing answers. That judgement is the
            # model's to make, so it stands, and the reader is told the
            # documents do not say.
            result.reason = "nothing"

    logger.info({"event": "coordinator.plan", "reason": result.reason,
                 "searches": len(result.searches), "tool_calls": result.tool_calls,
                 "found": len(found), "passages": len(result.passages),
                 "documents": len(result.assignments)})
    return result


async def _assignments(scope: Scope, delegate: List[Delegation],
                       found: Dict[str, Dict[str, Any]]) -> List[Assignment]:
    names = {p.get("file_id"): p.get("file_name") for p in found.values()}
    if any(d.document_id not in names for d in delegate):
        # Named from list_documents rather than found in a search. Only a
        # document the reader may see has a name here.
        for doc in await scope.documents():
            names.setdefault(doc["id"], doc.get("file_name"))
    out: List[Assignment] = []
    for d in delegate:
        if any(a.file_id == d.document_id for a in out):
            continue
        name = names.get(d.document_id)
        if name is None:
            continue
        out.append(Assignment(d.document_id, name, d.find))
        if len(out) >= MAX_DOCUMENTS:
            break
    return out
