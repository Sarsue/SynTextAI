"""The answer agent: how a question becomes a cited answer.

    process_query ─► retrieve ─► plan ─┬─► select_context ─► generate ──┬─► verify ─► render
                                       │   (one document)                │
                                       └─► document_worker × N ─► write ─┘
                                           (several, in parallel)

Four agents, each with one job and its own model setting (api/agents/models.py):

    coordinator      plan: which documents does this question need?
    document worker  answer from one document only
    writer           combine the workers' answers into one
    verifier         does each cited page say what the answer says?

The single-document path is the pipeline this replaced, unchanged: one broad
search, the best passages, one answer. Most questions take it. The
coordinator sends a question down the other path only when it needs more than
one document, and the verifier checks both.

WHAT THIS REPLACED, AND THE NUMBERS A CHANGE HAS TO BEAT

A search loop used to sit where the coordinator is: split the question into
needs, search again for any need the first search had not covered. On the
citation benchmark it scored 21.0 against 19.0 for a single search (range 19-22
against 18-21), and moved multi-document questions from 3.7 to 4.8 of 10. It
never shipped: +2.0 is inside the benchmark's own noise. Workers take the same
idea further, a search aimed at each need, and add what the loop could not:
a separate, small answer per document.

Before that, a tool-calling agent that chose its own searches scored 16.2
against the pipeline's 17.0 calling the same search, and a model judging
whether a need was covered took it to 11.2. So here the model decides WHICH
documents, and code decides everything the retriever can decide better.
"""
from __future__ import annotations

import logging
import operator
import os
from typing import Annotated, Any, Dict, List, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Send
from typing_extensions import TypedDict

from api.agents import coordinator, verifier, writer
from api.agents.document_worker import WorkerResult, answer_from_document
from api.agents.evidence import EvidenceSet
from api.agents.models import WORKER_MODEL
from api.core.log_safety import safe_text
from api.rag.chunk_selector import SmartChunkSelector
from api.rag.query_processor import DefaultQueryProcessor
from api.services.llm_service import MAX_TOKENS_CONTEXT, get_text_embedding

logger = logging.getLogger(__name__)

query_processor = DefaultQueryProcessor()
chunk_selector = SmartChunkSelector()

# Room for the retrieved pages, leaving the rest of the window for the
# instructions, the conversation history and the generated answer.
CONTEXT_TOKEN_BUDGET = max(3000, int(MAX_TOKENS_CONTEXT * 0.5))

# How many chunks the broad search returns. Swept against the pages the
# benchmark knows are correct, then end to end:
#
#     top_k   retrieval recall   citations (3 runs)
#        25             23/27    19.0 (18-21)
#        40             25/27    17.7 (16-19)
#
# Two more questions arrived with every source they needed, and fewer were
# answered correctly. More of the same ranked list hurts this model; a search
# aimed at a different need helps. Twenty-five stays.
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "25"))


class AnswerState(TypedDict, total=False):
    user_id: int
    message: str
    formatted_history: str
    language: str
    comprehension_level: str
    workspace_id: Optional[int]
    file_id: Optional[int]
    accessible_ids: Optional[List[int]]

    rewritten_query: str
    expanded_terms: List[str]
    evidence: Any
    plan: Any

    # Each worker appends its result; the writer reads them all.
    worker_results: Annotated[List[Any], operator.add]

    context_chunks: List[Dict[str, Any]]
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
        g.add_node("process_query", self._process_query)
        g.add_node("retrieve", self._retrieve)
        g.add_node("plan", self._plan)
        g.add_node("select_context", self._select_context)
        g.add_node("generate", self._generate)
        g.add_node("document_worker", self._document_worker)
        g.add_node("write", self._write)
        g.add_node("verify", self._verify)
        g.add_node("render", self._render)

        g.set_entry_point("process_query")
        g.add_edge("process_query", "retrieve")
        g.add_edge("retrieve", "plan")
        g.add_conditional_edges("plan", self._route, ["select_context", "document_worker"])
        g.add_edge("select_context", "generate")
        g.add_edge("generate", "verify")
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
    ) -> Dict[str, Any]:
        final: AnswerState = await self._graph.ainvoke({
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
            "rewritten_query": final.get("rewritten_query", message),
            "expanded_terms": final.get("expanded_terms", []),
            "mode": final.get("mode", "single"),
            "plan": plan.reason if plan else None,
            # Per document: whether it answered and how much it read. The
            # difference between "the coordinator picked the wrong document"
            # and "the right document did not say" is in here.
            "workers": [
                {"file_id": w.file_id, "kind": w.draft.kind, "chunks": len(w.chunks)}
                for w in workers
            ],
            "verification": final.get("verification"),
        }

    async def _process_query(self, state: AnswerState) -> AnswerState:
        message = state.get("message") or ""
        rewritten, expanded = await query_processor.process(message, state.get("formatted_history"))
        logger.info({
            "event": "answer_agent.process_query",
            "rewritten_query": safe_text(rewritten, "r"),
            "expanded_terms_count": len(expanded or []),
        })
        # Retrieval is scoped by workspace, not by uploader. Without this an
        # invited staff member matched zero chunks, because the documents
        # belong to the owner who uploaded them.
        accessible = None
        if state.get("workspace_id") is None:
            accessible = await self._store.workspace_repo.accessible_workspace_ids(state["user_id"])
        return {"rewritten_query": rewritten, "expanded_terms": expanded or [],
                "accessible_ids": accessible}

    async def _search(self, state: AnswerState, query: str, top_k: int) -> List[Dict[str, Any]]:
        emb = await get_text_embedding(query)
        return await self._store.file_repo.hybrid_search(
            user_id=state["user_id"],
            query=query,
            query_embedding=emb,
            workspace_id=state.get("workspace_id"),
            file_id=state.get("file_id"),
            top_k=top_k,
            accessible_workspace_ids=state.get("accessible_ids"),
        ) or []

    async def _retrieve(self, state: AnswerState) -> AnswerState:
        """One broad search across everything the asker can see.

        Plus a small search per expanded term, APPENDED after the main results
        and ranked as one list, exactly as the pipeline before this did. Adding
        each term search to the evidence set as its own retrieval looks
        equivalent and is not: rank fusion gives a term search's first hit the
        same weight as the main search's first hit, so five loosely related
        passages per term jumped ahead of the main search's ranks 6 to 25.
        That was measured, by accident, on 2026-09-22: every HVAC question
        that regressed took this single-document path (error codes, charging
        charts), and nothing else on the path had changed.
        """
        query = state.get("rewritten_query") or state.get("message") or ""
        results = list(await self._search(state, query, RETRIEVAL_TOP_K))
        for term in (state.get("expanded_terms") or [])[:3]:
            try:
                results.extend(await self._search(state, term, 5))
            except Exception as e:
                logger.warning({"event": "answer_agent.expansion_term_error", "error": str(e)[:200]})

        # Dedupe by (file, segment) keeping the first, highest-ranked copy,
        # as the old pipeline did before handing one list to the evidence set.
        seen, unique = set(), []
        for r in results:
            key = (r.get("file_id"), r.get("segment_id") if r.get("segment_id") is not None else r.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        evidence = EvidenceSet()
        evidence.add(unique, query)
        logger.info({"event": "answer_agent.retrieve", "results": len(results), "evidence": len(evidence)})
        return {"evidence": evidence}

    async def _plan(self, state: AnswerState) -> AnswerState:
        evidence: EvidenceSet = state["evidence"]
        plan = await coordinator.plan(
            state.get("message") or "",
            evidence.as_chunks(),
            scoped=state.get("file_id") is not None,
        )
        return {"plan": plan}

    def _route(self, state: AnswerState):
        plan = state["plan"]
        if not plan.is_multi:
            return "select_context"
        evidence = state["evidence"].as_chunks()
        return [
            Send("document_worker", {
                "state": state,
                "assignment": a,
                "seed": [c for c in evidence if c.get("file_id") == a.file_id],
            })
            for a in plan.assignments
        ]

    async def _document_worker(self, task: Dict[str, Any]) -> AnswerState:
        state, a = task["state"], task["assignment"]
        result = await answer_from_document(
            store=self._store,
            composer=self._composer,
            user_id=state["user_id"],
            workspace_id=state.get("workspace_id"),
            accessible_workspace_ids=state.get("accessible_ids"),
            file_id=a.file_id,
            file_name=a.file_name,
            question=state.get("message") or "",
            focus=a.focus,
            formatted_history=state.get("formatted_history") or "",
            language=state.get("language") or "English",
            comprehension_level=state.get("comprehension_level") or "beginner",
            seed=task["seed"],
        )
        return {"worker_results": [result]}

    async def _write(self, state: AnswerState) -> AnswerState:
        results: List[WorkerResult] = state.get("worker_results") or []
        # Workers finish in any order; the coordinator's order is the one
        # that says which document matters most.
        order = {a.file_id: i for i, a in enumerate(state["plan"].assignments)}
        results = sorted(results, key=lambda r: order.get(r.file_id, len(order)))
        chunks = [c for r in results for c in r.chunks]

        if not any(r.answered for r in results):
            # No single document answered. The broad context still might, so
            # fall back to the single path rather than refusing.
            logger.info({"event": "answer_agent.workers_empty_fallback"})
            fallback = await self._select_context(state)
            state = {**state, **fallback}
            out = await self._generate(state)
            return {**fallback, **out, "mode": "multi_fallback"}

        draft = await writer.write(state.get("message") or "", results)
        return {"draft": draft, "context_chunks": chunks, "mode": "multi"}

    async def _select_context(self, state: AnswerState) -> AnswerState:
        evidence: EvidenceSet = state["evidence"]
        chunks = chunk_selector.select(
            evidence.as_chunks(),
            state.get("rewritten_query") or state.get("message") or "",
            token_budget=CONTEXT_TOKEN_BUDGET,
        )
        logger.info({"event": "answer_agent.select_context", "selected_chunks": len(chunks)})
        return {"context_chunks": chunks}

    async def _generate(self, state: AnswerState) -> AnswerState:
        draft = await self._composer.compose(
            state.get("message") or "",
            state.get("formatted_history") or "",
            state.get("context_chunks") or [],
            state.get("language") or "English",
            state.get("comprehension_level") or "beginner",
            model=WORKER_MODEL,
        )
        logger.info({"event": "answer_agent.generate", "kind": draft.kind})
        return {"draft": draft, "mode": state.get("mode") or "single"}

    async def _verify(self, state: AnswerState) -> AnswerState:
        draft, report = await verifier.verify(state["draft"])
        return {"draft": draft, "verification": report.as_dict()}

    async def _render(self, state: AnswerState) -> AnswerState:
        return {"response": self._composer.render(state["draft"])}
