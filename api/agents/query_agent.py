import logging

from api.core.log_safety import safe_text
import os
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from api.agents.evidence import EvidenceSet
from api.rag.chunk_selector import SmartChunkSelector
from api.rag.query_processor import DefaultQueryProcessor
from api.services.llm_service import MAX_TOKENS_CONTEXT, get_text_embedding

logger = logging.getLogger(__name__)


# The two pieces this agent actually uses. They used to be reached through
# a RAGPipeline that existed only to hold them, built by a factory that
# existed only to build it, behind interfaces with one implementation each.
query_processor = DefaultQueryProcessor()
chunk_selector = SmartChunkSelector()

# Room for the retrieved pages, leaving the rest of the window for the
# instructions, the conversation history and the generated answer. Fifteen
# pages of a US federal PDF run about 7k tokens, so this holds all of them
# with room to spare rather than cutting the list at six.
CONTEXT_TOKEN_BUDGET = max(3000, int(MAX_TOKENS_CONTEXT * 0.5))

# Measured against the citation benchmark, counting only whether the page a
# correct answer needs is present at all:
#
#   top_k   all expected pages present
#      15   18/21
#      25   18/21
#      40   20/21
#
# A per-document cap was tried here to stop a 98-page handbook taking most of
# the slots, and lost at every top_k, so there is none: room, not rationing.
# How many chunks reach the model. 25 was chosen when a chunk was a whole page;
# chunks are now about 2.4 times smaller, so the same number covered eleven
# pages instead of twenty-five and the retrieval budget had been cut by more
# than half without anyone deciding that.
#
# Swept against the pages the benchmark knows are correct, with no model in the
# loop:
#
#     top_k   single 17   multi 10   all 27   approx tokens
#        25          17          6       23           7,750
#        40          17          8       25          12,400
#        60          17          8       25          18,600
#       100          17          8       25          31,000
#
# 40 is the knee for recall: it buys everything 100 does. So it was tried, and
# it made the answers worse:
#
#     top_k   retrieval recall   citations (3 runs)
#        25             23/27    19.0 (18-21)
#        40             25/27    17.7 (16-19)
#
# Two more questions arrived with every source they needed, and fewer were
# answered correctly. So recall is not the binding constraint here and buying
# more of it costs something. Twenty-five stays.
#
# This looked at the time like a fifth instance of one pattern: eight passages
# beat twenty, the evidence selector's extra stage lost, and better recall
# lost. The loop then won on chunk-sized units, 21.0 against 19.0, which
# separates the two things that had been conflated. Adding more of the SAME
# ranked list hurts, every time it has been tried. Adding a list aimed at a
# different information need helps. The lever is aim, not volume, and this
# constant governs the first kind.
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "25"))

# How many times one question may retrieve, in total.
#
# ONE, and the reason is a condition rather than a result.
#
# Measured on chunk-sized retrieval units, three runs against four:
#
#                        citations /27   single /17   multi /10
#     one retrieval       19.0 (18-21)  15.3 (14-16)  3.7 (2-5)
#     up to three         21.0 (19-22)  16.2 (14-17)  4.8 (4-5)
#
# +2.0 is inside the +/-3 this benchmark moves on its own, so it is not proven
# by that rule. What supports it is the shape: every one of four loop runs
# scored at or above the single-retrieval mean, and multi-document, the metric
# the loop exists for, has a floor above that mean.
#
# It also reconciles four earlier results that all found this model worse with
# more context. top_k 40 added fifteen chunks from the same ranked list and
# scored 17.7; this adds a retrieval aimed at a different information need and
# scores 21.0. Comparable extra context, opposite outcome. The lever is not how
# much context, it is whether the context was aimed at something.
#
# What is NOT understood: the five questions diagnosed as this loop's targets,
# 16, 17, 28, 30 and 31, did not move. The gain is real and does not come from
# where it was predicted to, so the number is trusted further than the story
# behind it.
#
# So why one. The same loop was measured on PAGE-sized units and lost, 15.3
# against 17.0. Every document uploaded before chunk-level retrieval still has
# page-sized chunks until it is re-ingested, so turning this on now would apply
# the loop to exactly the conditions where it has been measured to regress.
#
# Raise it to 3 once a workspace's documents have been re-ingested. The setting
# is per-deployment and the benefit is conditional on the data, which is not a
# distinction a default can express.
MAX_RETRIEVALS = int(os.getenv("MAX_RETRIEVALS", "1"))
# Attempts at a single need before accepting that the documents do not cover it.
COVERAGE_ATTEMPTS = int(os.getenv("COVERAGE_ATTEMPTS", "2"))


class QueryAgentState(TypedDict, total=False):
    user_id: int
    message: str
    formatted_history: str
    language: str
    comprehension_level: str
    workspace_id: Optional[int]
    file_id: Optional[int]

    rewritten_query: str
    expanded_terms: List[str]

    retrieved_results: List[Dict[str, Any]]
    unique_results: List[Dict[str, Any]]
    context_chunks: List[Dict[str, Any]]

    # The evidence set is the state of this graph. Retrieval adds to it, the
    # coverage check reads it, the answer is written from it.
    evidence: Any
    information_needs: List[str]
    need_attempts: Dict[str, int]
    covered_needs: List[str]
    retrievals: int
    next_query: str
    last_query: str
    last_added: int

    response: str
    mode: str


class QueryAgent:
    def __init__(self, *, store: Any, syntext: Any):
        self._store = store
        self._syntext = syntext
        self._graph = self._build_graph()

    def _build_graph(self):
        workflow: StateGraph = StateGraph(QueryAgentState)

        workflow.add_node("process_query", self._process_query)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("dedupe_and_normalize", self._dedupe_and_normalize)
        workflow.add_node("check_coverage", self._check_coverage)
        workflow.add_node("select_context", self._select_context)
        workflow.add_node("generate", self._generate)

        workflow.set_entry_point("process_query")
        workflow.add_edge("process_query", "retrieve")
        workflow.add_edge("retrieve", "dedupe_and_normalize")
        # No rerank stage. What sat here called itself a cross-encoder but
        # re-embedded content[:600] with the same bi-encoder that produced the
        # retrieval score, so it could never beat that score and routinely
        # destroyed it: the ADA guide states its 15-employee threshold at
        # character 2460 of the page, and the first 600 characters are a block
        # of phone numbers, so the one page holding the answer was scored on
        # text that does not contain it and pushed out of the context.
        workflow.add_edge("dedupe_and_normalize", "check_coverage")
        # The only loop, and the only thing that distinguishes what used to be
        # two architectures. A question whose needs are all covered by the first
        # retrieval goes straight on and behaves exactly as this pipeline did
        # before, which is most questions. One with a need nothing has answered
        # yet searches again for that need specifically.
        workflow.add_conditional_edges(
            "check_coverage",
            self._needs_more_evidence,
            {"retrieve": "retrieve", "answer": "select_context"},
        )
        workflow.add_edge("select_context", "generate")
        workflow.add_edge("generate", END)

        return workflow.compile()

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
        initial: QueryAgentState = {
            "user_id": user_id,
            "message": message,
            "formatted_history": formatted_history,
            "language": language,
            "comprehension_level": comprehension_level,
            "workspace_id": workspace_id,
            "file_id": file_id,
        }

        final_state: QueryAgentState = await self._graph.ainvoke(initial)
        return {
            "response": final_state.get("response", ""),
            "context_chunks": final_state.get("context_chunks", []),
            "rewritten_query": final_state.get("rewritten_query", message),
            "expanded_terms": final_state.get("expanded_terms", []),
            "mode": "pipeline",
            "information_needs": final_state.get("information_needs", []),
            "covered_needs": final_state.get("covered_needs", []),
            "retrievals": final_state.get("retrievals", 1),
        }

    async def _process_query(self, state: QueryAgentState) -> QueryAgentState:
        message = state.get("message") or ""
        formatted_history = state.get("formatted_history")

        rewritten_query, expanded_terms = await query_processor.process(message, formatted_history)
        logger.info(
            {
                "event": "query_agent.process_query",
                "message": safe_text(message),
                "rewritten_query": safe_text(rewritten_query, "r"),
                "expanded_terms_count": len(expanded_terms or []),
            }
        )
        # Only worth asking when something can act on the answer. With the
        # retrieval cap at one, splitting the question costs a model call and
        # changes nothing, and it switches itself back on the moment the cap is
        # raised rather than needing to be remembered.
        needs = [message]
        if MAX_RETRIEVALS > 1:
            needs = await query_processor.information_needs(message)
            # Counted, not named. An information need is a fragment of the
            # customer's question and carries the same PHI or privilege the
            # question does.
            logger.info({
                "event": "query_agent.information_needs",
                "count": len(needs),
                "needs": [safe_text(n, "n") for n in needs],
            })
        return {
            "rewritten_query": rewritten_query,
            "expanded_terms": expanded_terms or [],
            "information_needs": needs,
            "need_attempts": {},
            "covered_needs": [],
            "retrievals": 0,
            "evidence": EvidenceSet(),
            "next_query": "",
        }

    async def _retrieve(self, state: QueryAgentState) -> QueryAgentState:
        user_id = state["user_id"]
        workspace_id = state.get("workspace_id")
        file_id = state.get("file_id")

        # First pass searches the question; later passes search whichever need
        # nothing has answered yet.
        rewritten_query = (
            state.get("next_query")
            or state.get("rewritten_query")
            or state.get("message")
            or ""
        )
        # Expansion is worth its round trips once, on the question itself.
        expanded_terms = state.get("expanded_terms") or [] if not state.get("next_query") else []

        query_embedding = await get_text_embedding(rewritten_query)

        # Retrieval is scoped by workspace, not by uploader. Without this an
        # invited staff member matched zero chunks, because the documents belong
        # to the owner who uploaded them, and so got no answers at all.
        accessible_ids = None
        if workspace_id is None:
            accessible_ids = await self._store.workspace_repo.accessible_workspace_ids(user_id)

        vector_results = await self._store.file_repo.hybrid_search(
            user_id=user_id,
            query=rewritten_query,
            query_embedding=query_embedding,
            workspace_id=workspace_id,
            file_id=file_id,
            top_k=RETRIEVAL_TOP_K,
            accessible_workspace_ids=accessible_ids,
        )

        additional_results: List[Dict[str, Any]] = []
        for term in (expanded_terms[:3] if expanded_terms else []):
            try:
                term_embedding = await get_text_embedding(term)
                term_results = await self._store.file_repo.hybrid_search(
                    user_id=user_id,
                    query=term,
                    query_embedding=term_embedding,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    top_k=5,
                    accessible_workspace_ids=accessible_ids,
                )
                additional_results.extend(term_results)
            except Exception as term_error:
                logger.warning(
                    {
                        "event": "query_agent.retrieve.expansion_term_error",
                        "term": term,
                        "error": str(term_error),
                    }
                )

        all_results = (vector_results or []) + additional_results
        logger.info(
            {
                "event": "query_agent.retrieve",
                "rewritten_query": safe_text(rewritten_query, "r"),
                "vector_results": len(vector_results or []),
                "additional_results": len(additional_results),
                "combined_results": len(all_results),
            }
        )

        return {
            "retrieved_results": all_results,
            "retrievals": int(state.get("retrievals") or 0) + 1,
            "last_query": rewritten_query,
        }

    async def _dedupe_and_normalize(self, state: QueryAgentState) -> QueryAgentState:
        all_results = state.get("retrieved_results") or []

        seen_keys = set()
        unique_results: List[Dict[str, Any]] = []
        for result in all_results:
            seg_id = result.get("segment_id")
            chunk_id = result.get("chunk_id")
            result_file_id = result.get("file_id")

            dedup_key = (result_file_id, seg_id) if seg_id is not None else (result_file_id, chunk_id)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            unique_results.append(result)

        for r in unique_results:
            if "similarity_score" in r:
                continue
            if r.get("hybrid_score") is not None:
                r["similarity_score"] = float(r.get("hybrid_score") or 0.0)
            else:
                r["similarity_score"] = 0.0

        evidence: EvidenceSet = state.get("evidence") or EvidenceSet()
        added = evidence.add(unique_results, state.get("last_query") or "")

        logger.info(
            {
                "event": "query_agent.accumulate",
                "input_results": len(all_results),
                "unique_results": len(unique_results),
                "new_passages": len(added),
                "evidence_total": len(evidence),
            }
        )
        return {
            "unique_results": unique_results,
            "evidence": evidence,
            "last_added": len(added),
        }

    async def _check_coverage(self, state: QueryAgentState) -> QueryAgentState:
        """Which needs have evidence, decided by retrieval rather than judgement.

        A need counts as covered when a search aimed at it contributed passages
        the set did not already hold. No model decides that. The retriever is
        the judge, which is the component that has actually been measured to
        work, and the alternative was a classifier that regressed the agent
        from 16.2 to 11.2 while looking perfectly reasonable on four questions.

        Two attempts before a need is written off, because one search finding
        nothing is as often a badly phrased query as an absent answer: asked
        how to register a trademark, one search found nothing and the SBA guide
        covers it on page 10.
        """
        needs = list(state.get("information_needs") or [])
        attempts = dict(state.get("need_attempts") or {})
        covered = set(state.get("covered_needs") or [])
        added = int(state.get("last_added") or 0)
        searched = state.get("last_query") or ""

        # Coverage is ensured by construction, not inferred. Crediting the
        # first broad search with covering every need is what the first version
        # did, and it meant a two-need question never got its second search:
        # the loop existed and could not fire.
        #
        # So every need gets a search aimed at it. A question with one need is
        # already served by the first search and never loops, which is most
        # questions and exactly the pipeline this replaces. A question with two
        # needs gets a second retrieval, which is the case a single ranked list
        # cannot serve and the reason any of this exists.
        if state.get("next_query"):
            attempts[searched] = attempts.get(searched, 0) + 1
            if added > 0:
                covered.add(searched)
        elif len(needs) <= 1:
            # The broad search was that need's search.
            for n in needs:
                attempts[n] = attempts.get(n, 0) + 1
                if added > 0:
                    covered.add(n)

        pending = [
            n for n in needs
            if n not in covered and attempts.get(n, 0) < COVERAGE_ATTEMPTS
        ]
        logger.info({
            "event": "query_agent.coverage",
            "needs": len(needs), "covered": len(covered),
            "pending": [safe_text(n, "n") for n in pending[:3]],
            "retrievals": state.get("retrievals"),
        })
        return {
            "need_attempts": attempts,
            "covered_needs": sorted(covered),
            "next_query": pending[0] if pending else "",
        }

    def _needs_more_evidence(self, state: QueryAgentState) -> str:
        """Search again, or answer. Bounded on total retrievals, not per need.

        Per-need bounding lets four needs times two attempts become eight
        searches on one question. The ceiling is on the whole question so the
        worst case stays near the cost of the pipeline this replaces.
        """
        if not state.get("next_query"):
            return "answer"
        if int(state.get("retrievals") or 0) >= MAX_RETRIEVALS:
            logger.info({"event": "query_agent.retrieval_cap_reached"})
            return "answer"
        return "retrieve"

    async def _select_context(self, state: QueryAgentState) -> QueryAgentState:
        rewritten_query = state.get("rewritten_query") or state.get("message") or ""
        evidence: EvidenceSet = state.get("evidence") or EvidenceSet()
        # One ranked list, however many retrievals produced it.
        candidates = evidence.as_chunks() or (state.get("unique_results") or [])

        # The budget used to be the selector's 3000-token default while
        # MAX_TOKENS_CONTEXT is 120000, so roughly six of fifteen retrieved
        # chunks reached the model and the rest were discarded for no reason.
        # Leave headroom for the citation instructions, the history and the
        # answer itself; query_pipeline reduces again if it still overruns.
        context_chunks = chunk_selector.select(
            candidates,
            rewritten_query,
            token_budget=CONTEXT_TOKEN_BUDGET,
        )

        logger.info(
            {
                "event": "query_agent.select_context",
                "selected_chunks": len(context_chunks or []),
            }
        )

        return {"context_chunks": context_chunks}

    async def _generate(self, state: QueryAgentState) -> QueryAgentState:
        message = state.get("message") or ""
        formatted_history = state.get("formatted_history") or ""
        context_chunks = state.get("context_chunks") or []
        language = state.get("language") or "English"
        comprehension_level = state.get("comprehension_level") or "beginner"

        response = await self._syntext.query_pipeline(
            message,
            formatted_history,
            context_chunks,
            language,
            comprehension_level,
        )

        logger.info(
            {
                "event": "query_agent.generate",
                "has_response": bool(response),
                "context_chunks": len(context_chunks),
            }
        )

        return {
            "response": response,
            "mode": "enhanced",
        }
