import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


class IngestionAgentState(TypedDict, total=False):
    user_id: int
    file_id: int
    filename: str
    file_url: str
    workspace_id: Optional[int]
    language: str
    comprehension_level: str

    result: Dict[str, Any]
    mode: str


class IngestionAgent:
    """LangGraph wrapper around the ingestion pipeline.

    This first thin-slice keeps the existing ingestion implementation intact and
    provides a graph orchestration layer + structured trace logs.
    """

    def __init__(self, *, process_fn: Callable[..., Awaitable[Dict[str, Any]]]):
        self._process_fn = process_fn
        self._graph = self._build_graph()

    def _build_graph(self):
        workflow: StateGraph = StateGraph(IngestionAgentState)

        workflow.add_node("ingest", self._ingest)
        workflow.set_entry_point("ingest")
        workflow.add_edge("ingest", END)

        return workflow.compile()

    async def run(
        self,
        *,
        user_id: int,
        file_id: int,
        filename: str,
        file_url: str,
        workspace_id: int | None = None,
        language: str = "en",
        comprehension_level: str = "Beginner",
    ) -> Dict[str, Any]:
        initial: IngestionAgentState = {
            "user_id": user_id,
            "file_id": file_id,
            "filename": filename,
            "file_url": file_url,
            "workspace_id": workspace_id,
            "language": language,
            "comprehension_level": comprehension_level,
        }

        final_state: IngestionAgentState = await self._graph.ainvoke(initial)
        result = final_state.get("result") or {"success": False, "error_message": "missing_result"}
        return result

    async def _ingest(self, state: IngestionAgentState) -> IngestionAgentState:
        logger.info(
            {
                "event": "ingestion_agent.start",
                "file_id": state.get("file_id"),
                "filename": state.get("filename"),
            }
        )

        result = await self._process_fn(
            user_id=state["user_id"],
            file_id=state["file_id"],
            filename=state["filename"],
            file_url=state["file_url"],
            workspace_id=state.get("workspace_id"),
            language=state.get("language") or "en",
            comprehension_level=state.get("comprehension_level") or "Beginner",
        )

        logger.info(
            {
                "event": "ingestion_agent.done",
                "file_id": state.get("file_id"),
                "success": bool(result.get("success")),
                "final_status": result.get("final_status"),
            }
        )

        return {"result": result, "mode": "agent"}
