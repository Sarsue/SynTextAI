"""A document worker: an agent that searches ONE document until it has what
the coordinator sent it for, then answers from that document alone.

WHY ONE DOCUMENT PER WORKER

Every measurement on this model points the same way: it does worse with more
text in one prompt and better when each prompt is aimed at one thing. A
worker's search is confined to one document, so a manual that is ranked
twelfth across the workspace is ranked first inside itself, and the answer it
writes is never distracted by four similar manuals.

HOW IT SEARCHES

Like the coordinator, the model controls it: it writes the queries, judges the
passages and searches again. It starts from what the coordinator already found
in this document, and its tools cannot leave the document: the scope is one
file, and tools.Scope ignores any other id the model names.

WHAT IT RETURNS

A Draft from the same compose step the single path uses, so the grounding
rules, the refusal word and the citation markers are identical and nothing
about writing an answer exists twice. A worker whose document does not answer
returns the refusal, and the writer leaves it out.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from api.agents import chat_models
from api.agents.models import WORKER_MODEL
from api.agents.progress import NO_PROGRESS, Progress
from api.agents.tools import Scope, _listing, document_tools, found_passages, passage_id, searches_made
from api.rag.chunk_selector import SmartChunkSelector
from api.services.answer_composer import Draft
from api.services.llm_service import MAX_TOKENS_CONTEXT

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = int(os.getenv("WORKER_MAX_TOOL_CALLS", "4"))
# Always more than tool calls, so the last turn can decide (coordinator.py).
MAX_MODEL_CALLS = MAX_TOOL_CALLS + 2
DEADLINE = float(os.getenv("WORKER_DEADLINE", "30"))
MAX_PASSAGES = 10

# A worker's share of the context when it answers from everything it read.
# Smaller than the single path's on purpose: a small prompt aimed at one
# document is the whole reason the worker exists.
WORKER_TOKEN_BUDGET = max(2000, int(MAX_TOKENS_CONTEXT * 0.1))

_selector = SmartChunkSelector()


class Passages(BaseModel):
    """Your decision, once you have searched this document enough."""

    passage_ids: List[str] = Field(
        default_factory=list,
        description=("Ids of the passages from this document that answer, most "
                     "important first. Empty if this document does not answer."),
    )


def _prompt(file_name: str, file_id: int) -> str:
    return f"""You find the passages in ONE document, "{file_name}" (document_id {file_id}), that answer a question. Another step writes the answer from the passages you choose.

- Search this document with search_document. Phrase queries the way the document would say it.
- Be fast: make all the searches you already know you need in ONE turn, as several tool calls at once.
- Judge what comes back: a passage counts only if it states what is asked. When nothing answers, search again with different words, or read_page when a passage is cut off or a table continues.
- Every passage you have read stays available by its id, so never repeat a search.
- Stop as soon as you have what is needed. You have at most {MAX_TOOL_CALLS} tool calls; after that you must decide.

Then give passage_ids, most important first, or an empty list if this document does not answer."""


@dataclass
class WorkerResult:
    file_id: int
    file_name: str
    focus: str
    draft: Draft
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    searches: List[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.draft.kind in ("answer", "uncited")


def _agent(scope: Scope, file_id: int, file_name: str, progress: Progress, seen: set):
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
    from langchain.agents.structured_output import ToolStrategy

    return create_agent(
        chat_models.chat_model(WORKER_MODEL),
        document_tools(scope, progress, names={file_id: file_name}, seen=seen),
        system_prompt=_prompt(file_name, file_id),
        response_format=ToolStrategy(Passages),
        middleware=[
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
            chat_models.require_tool_call("Passages"),
        ],
        name="document_worker",
    )


async def answer_from_document(
    *,
    scope: Scope,
    composer: Any,
    file_id: int,
    file_name: str,
    question: str,
    focus: str,
    formatted_history: str,
    language: str,
    comprehension_level: str,
    seed: List[Dict[str, Any]],
    progress: Progress = NO_PROGRESS,
) -> WorkerResult:
    own = replace(scope, file_id=file_id)
    asked = question if not focus or focus == question else (
        f"{question}\n\n(From this document, find: {focus}. Other documents cover "
        "the rest, so answer only what this one says.)"
    )
    opening = asked
    seen: set = set()
    if seed:
        # Marked as read, so a search that finds them again says so.
        opening += ("\n\nAlready found in this document:\n\n"
                    + _listing(seed, "", seen))

    found: Dict[str, Dict[str, Any]] = {passage_id(p): p for p in seed}
    chosen: List[Dict[str, Any]] = []
    decided = False
    searches: List[str] = []
    try:
        state, _ = await chat_models.run_agent(
            _agent(own, file_id, file_name, progress, seen),
            {"messages": [{"role": "user", "content": opening}]},
            DEADLINE,
        )
        messages = state.get("messages") or []
        searches = searches_made(messages)
        for pid, p in found_passages(messages).items():
            found.setdefault(pid, p)
        decision = state.get("structured_response")
        if decision is not None:
            decided = True
            chosen = [found[i] for i in dict.fromkeys(decision.passage_ids) if i in found][:MAX_PASSAGES]
    except Exception as e:
        # Whatever the coordinator already found here is still this
        # document's evidence.
        logger.warning({"event": "document_worker.failed", "file_id": file_id, "error": str(e)[:300]})

    if not decided:
        # Cut off or failed before deciding: answer from everything it read.
        # A decision that this document does not answer stands, and the
        # empty list becomes the refusal the writer leaves out.
        chosen = _selector.select(list(found.values()), question, token_budget=WORKER_TOKEN_BUDGET)
    draft = await composer.compose(
        asked, formatted_history, chosen, language, comprehension_level, model=WORKER_MODEL,
    )
    logger.info({"event": "document_worker.done", "file_id": file_id, "kind": draft.kind,
                 "chunks": len(chosen), "searches": len(searches)})
    return WorkerResult(file_id, file_name, focus, draft, chosen, searches)
