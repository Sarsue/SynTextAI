"""The answer agent: how a question becomes a cited answer.

    research ─┬─► answer ─────────────────────────────┬─► verify ─► render
              │   (the passages the coordinator chose) │
              └─► document_worker × N ─► write ────────┘
                  (several documents, in parallel)

Agentic retrieval, decided with Osas 2026-09-28: the model controls what is
searched and judges what comes back. Nothing here searches on its own.

Four agents, each with one job and its own model setting (api/agents/models.py):

    coordinator      search until the answer's passages are found; decide
                     whether one answer covers it or documents need workers
    document worker  search one document and answer from it alone
    writer           combine the workers' answers into one
    verifier         does each cited page say what the answer says?

The coordinator and the workers are LangChain agents (create_agent, with the
tools in tools.py). The writer and the verifier are single calls: they have
nothing to look up.
"""
from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Dict, List, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Send
from typing_extensions import TypedDict

from api.agents import coordinator, verifier, writer
from api.agents.document_worker import WorkerResult, answer_from_document
from api.agents.models import WORKER_MODEL
from api.agents.progress import NO_PROGRESS, Progress
from api.agents.tools import Scope
from api.rag.chunk_selector import SmartChunkSelector
from api.services.llm_service import MAX_TOKENS_CONTEXT

logger = logging.getLogger(__name__)

chunk_selector = SmartChunkSelector()

# Room for the passages when the answer is written from everything the
# coordinator read, because it ran out of time or calls before choosing.
CONTEXT_TOKEN_BUDGET = max(3000, int(MAX_TOKENS_CONTEXT * 0.5))


class AnswerState(TypedDict, total=False):
    user_id: int
    message: str
    formatted_history: str
    language: str
    comprehension_level: str
    workspace_id: Optional[int]
    file_id: Optional[int]

    scope: Any
    plan: Any

    # Each worker appends its result; the writer reads them all.
    worker_results: Annotated[List[Any], operator.add]

    context_chunks: List[Dict[str, Any]]
    # Where each step and the answer's text are reported as they happen.
    progress: Any
    draft: Any
    verification: Dict[str, Any]
    response: str
    mode: str


class AnswerAgent:
    def __init__(self, *, store: Any, composer: Any):
        self._store = store
        self._composer = composer
        self._graph = self._build_graph()

    def _build_graph(self):
        g: StateGraph = StateGraph(AnswerState)
        g.add_node("research", self._research)
        g.add_node("answer", self._answer)
        g.add_node("document_worker", self._document_worker)
        g.add_node("write", self._write)
        g.add_node("verify", self._verify)
        g.add_node("render", self._render)

        g.set_entry_point("research")
        g.add_conditional_edges("research", self._route, ["answer", "document_worker"])
        g.add_edge("answer", "verify")
        g.add_edge("document_worker", "write")
        g.add_edge("write", "verify")
        g.add_edge("verify", "render")
        g.add_edge("render", END)
        return g.compile()

    async def run(
        self,
        *,
        user_id: int,
        message: str,
        language: str,
        comprehension_level: str,
        formatted_history: str = "",
        workspace_id: int | None = None,
        file_id: int | None = None,
        progress: Optional[Progress] = None,
    ) -> Dict[str, Any]:
        final: AnswerState = await self._graph.ainvoke({
            "progress": progress or NO_PROGRESS,
            "user_id": user_id,
            "message": message,
            "formatted_history": formatted_history,
            "language": language,
            "comprehension_level": comprehension_level,
            "workspace_id": workspace_id,
            "file_id": file_id,
            "worker_results": [],
        })
        plan = final.get("plan")
        workers: List[WorkerResult] = final.get("worker_results") or []
        return {
            "response": final.get("response", ""),
            "context_chunks": final.get("context_chunks", []),
            "mode": final.get("mode", "single"),
            "plan": plan.reason if plan else None,
            # The queries the models chose, coordinator first, then each
            # worker's. The difference between "it searched for the wrong
            # thing" and "it found the page and the answer ignored it".
            "searches": (plan.searches if plan else []) + [q for w in workers for q in w.searches],
            # Per document: whether it answered and how much it read.
            "workers": [
                {"file_id": w.file_id, "kind": w.draft.kind, "chunks": len(w.chunks),
                 "searches": len(w.searches)}
                for w in workers
            ],
            "verification": final.get("verification"),
        }

    def _progress(self, state: AnswerState) -> Progress:
        return state.get("progress") or NO_PROGRESS

    async def _research(self, state: AnswerState) -> AnswerState:
        # Retrieval is scoped by workspace, not by uploader. Without this an
        # invited staff member matched zero chunks, because the documents
        # belong to the owner who uploaded them.
        accessible = None
        if state.get("workspace_id") is None:
            accessible = await self._store.workspace_repo.accessible_workspace_ids(state["user_id"])
        scope = Scope(
            store=self._store,
            user_id=state["user_id"],
            workspace_id=state.get("workspace_id"),
            accessible_ids=accessible,
            file_id=state.get("file_id"),
        )
        plan = await coordinator.plan(
            scope,
            state.get("message") or "",
            state.get("formatted_history") or "",
            progress=self._progress(state),
        )
        return {"scope": scope, "plan": plan}

    def _route(self, state: AnswerState):
        plan = state["plan"]
        if not plan.is_multi:
            return "answer"
        return [
            Send("document_worker", {
                "state": state,
                "assignment": a,
                "seed": [p for p in plan.found.values() if p.get("file_id") == a.file_id],
            })
            for a in plan.assignments
        ]

    async def _answer(self, state: AnswerState) -> AnswerState:
        plan = state["plan"]
        passages = plan.passages
        if plan.reason in ("gathered", "deadline"):
            # Everything it read, not a choice: fit it to the context.
            passages = chunk_selector.select(
                passages, state.get("message") or "", token_budget=CONTEXT_TOKEN_BUDGET,
            )
        self._progress(state).stage("writing")
        draft = await self._compose(state, passages, sink=self._progress(state))
        return {"draft": draft, "context_chunks": passages, "mode": "single"}

    async def _document_worker(self, task: Dict[str, Any]) -> AnswerState:
        state, a = task["state"], task["assignment"]
        result = await answer_from_document(
            scope=state["scope"],
            composer=self._composer,
            file_id=a.file_id,
            file_name=a.file_name,
            question=state.get("message") or "",
            focus=a.focus,
            formatted_history=state.get("formatted_history") or "",
            language=state.get("language") or "English",
            comprehension_level=state.get("comprehension_level") or "beginner",
            seed=task["seed"],
            progress=self._progress(state),
        )
        return {"worker_results": [result]}

    async def _write(self, state: AnswerState) -> AnswerState:
        results: List[WorkerResult] = state.get("worker_results") or []
        # Workers finish in any order; the coordinator's order is the one
        # that says which document matters most.
        order = {a.file_id: i for i, a in enumerate(state["plan"].assignments)}
        results = sorted(results, key=lambda r: order.get(r.file_id, len(order)))
        chunks = [c for r in results for c in r.chunks]

        progress = self._progress(state)
        progress.stage("writing")
        if not any(r.answered for r in results):
            # No single document answered. What the coordinator read across
            # all of them still might, so answer from that rather than refuse.
            logger.info({"event": "answer_agent.workers_empty_fallback"})
            passages = chunk_selector.select(
                list(state["plan"].found.values()), state.get("message") or "",
                token_budget=CONTEXT_TOKEN_BUDGET,
            )
            draft = await self._compose(state, passages, sink=progress)
            return {"draft": draft, "context_chunks": passages, "mode": "multi_fallback"}

        draft = await writer.write(state.get("message") or "", results, sink=progress)
        return {"draft": draft, "context_chunks": chunks, "mode": "multi"}

    async def _compose(self, state: AnswerState, context: List[Dict[str, Any]], sink: Any = None):
        draft = await self._composer.compose(
            state.get("message") or "",
            state.get("formatted_history") or "",
            context,
            state.get("language") or "English",
            state.get("comprehension_level") or "beginner",
            model=WORKER_MODEL,
            sink=sink,
        )
        logger.info({"event": "answer_agent.generate", "kind": draft.kind, "passages": len(context)})
        return draft

    async def _verify(self, state: AnswerState) -> AnswerState:
        draft = state["draft"]
        if draft.kind == "answer":
            claims = len(verifier.split_claims(draft.text))
            if claims:
                self._progress(state).stage("checking", claims=claims)
        draft, report = await verifier.verify(draft)
        return {"draft": draft, "verification": report.as_dict()}

    async def _render(self, state: AnswerState) -> AnswerState:
        return {"response": self._composer.render(state["draft"])}
