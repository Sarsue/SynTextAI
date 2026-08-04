import logging
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from api.rag.pipeline import RAGPipeline
from api.services.llm_service import MAX_TOKENS_CONTEXT, get_text_embedding

logger = logging.getLogger(__name__)


rag_pipeline = RAGPipeline(config={"search_engine": {"default_alpha": 0.7}})

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
RETRIEVAL_TOP_K = 25


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
        workflow.add_edge("dedupe_and_normalize", "select_context")
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
            "mode": final_state.get("mode", "enhanced"),
        }

    async def _process_query(self, state: QueryAgentState) -> QueryAgentState:
        message = state.get("message") or ""
        formatted_history = state.get("formatted_history")

        rewritten_query, expanded_terms = rag_pipeline.query_processor.process(message, formatted_history)
        logger.info(
            {
                "event": "query_agent.process_query",
                "message": message,
                "rewritten_query": rewritten_query,
                "expanded_terms_count": len(expanded_terms or []),
            }
        )
        return {
            "rewritten_query": rewritten_query,
            "expanded_terms": expanded_terms or [],
        }

    async def _retrieve(self, state: QueryAgentState) -> QueryAgentState:
        user_id = state["user_id"]
        workspace_id = state.get("workspace_id")
        file_id = state.get("file_id")

        rewritten_query = state.get("rewritten_query") or state.get("message") or ""
        expanded_terms = state.get("expanded_terms") or []

        query_embedding = get_text_embedding(rewritten_query)

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
                term_embedding = get_text_embedding(term)
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
                "rewritten_query": rewritten_query,
                "vector_results": len(vector_results or []),
                "additional_results": len(additional_results),
                "combined_results": len(all_results),
            }
        )

        return {"retrieved_results": all_results}

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

        logger.info(
            {
                "event": "query_agent.dedupe_and_normalize",
                "input_results": len(all_results),
                "unique_results": len(unique_results),
            }
        )

        return {"unique_results": unique_results}

    async def _select_context(self, state: QueryAgentState) -> QueryAgentState:
        rewritten_query = state.get("rewritten_query") or state.get("message") or ""
        candidates = state.get("unique_results") or []

        # The budget used to be the selector's 3000-token default while
        # MAX_TOKENS_CONTEXT is 120000, so roughly six of fifteen retrieved
        # chunks reached the model and the rest were discarded for no reason.
        # Leave headroom for the citation instructions, the history and the
        # answer itself; query_pipeline reduces again if it still overruns.
        context_chunks = rag_pipeline.chunk_selector.select(
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

        response = self._syntext.query_pipeline(
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
