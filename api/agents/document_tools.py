"""The tools a model may use to answer from a customer's documents.

WHY THIS EXISTS

The pipeline this replaces made every decision up front: one search, with one
phrasing of the question, and whatever came back was the answer's only evidence.
That works for "how long do i keep tax records" and cannot work for "what do i
need to do to keep employees safe and my premises accessible", which is two
searches in two documents. It also has nowhere to put the judgement "none of
this actually answers the question", so asked how to register a trademark it
produced a confident walkthrough of a database it had never seen.

A tool lets the model look, read what came back, and look again. That is the
whole point: retrieval stops being a fixed step and becomes something the model
does as many times as the question needs.

TENANCY

Read this before adding a tool.

The model never names a workspace. Scope is bound once, here, from the
authenticated request, and the schemas the model sees carry no workspace or user
argument at all. A model cannot ask for another company's documents because
there is no field in which to ask, and prompt injection inside an uploaded PDF
has nothing to inject into. Every tool goes through the same repository calls
the HTTP routes use, with the same accessible_workspace_ids scoping, so a tool
can never see more than the signed-in user could see by clicking.

`read_page` takes a file NAME rather than an id for the same reason. Ids are
guessable and belong to a global sequence; a name is resolved against the files
this caller can already list.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many passages one search returns. The fixed pipeline puts 25 in front of
# the model in a single shot; at 8 this agent was seeing a third of that per
# call, which is a plausible reason it cited the first page matching a phrase
# rather than the page defining the concept. Tunable so the question can be
# settled by running it rather than argued about.
MAX_SEARCH_RESULTS = int(os.getenv("TOOL_SEARCH_RESULTS", "8"))
MAX_PAGE_CHARS = 6000


def _cite_header(file_name: str, page: Any) -> str:
    """The heading above every passage, carrying the citation to copy.

    It used to read "--- osha_small_business_handbook.pdf, page 15 ---" and the
    model would helpfully render that as "OSHA handbook, page 15", which no
    longer names a file and so cannot be resolved to a link. Every such citation
    was dropped, and answers built on the right pages arrived with none. Spell
    out the exact string to copy, next to the text it belongs to.
    """
    return (
        f"--- {file_name}, page {page} ---\n"
        f"[cite this passage as: ({file_name}, page {page})]"
    )


# The schemas the model sees. No workspace_id, no user_id, no file_id: see the
# tenancy note above.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the customer's documents and return the most relevant "
                "passages, each with its file name and page number. Call this "
                "more than once when a question has separate parts: search each "
                "part in its own words rather than combining them into one "
                "query. If the results do not answer the question, say so "
                "instead of answering from your own knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look for, in the words the documents would "
                            "use. 'self-inspection checklist' finds more than "
                            "'how do i check my workplace'."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read one full page of one document. Use it to check a figure or "
                "a deadline before stating it, or to read around a passage that "
                "search returned only part of."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Exactly as it appears in search results or list_documents.",
                    },
                    "page": {"type": "integer", "description": "Page number, starting at 1."},
                },
                "required": ["file_name", "page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "List the documents available. Use it to find out whether a "
                "subject is covered at all before concluding it is missing."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class DocumentTools:
    """Executes tool calls against one caller's documents and nothing else."""

    def __init__(
        self,
        *,
        store: Any,
        user_id: int,
        workspace_id: Optional[int] = None,
        accessible_workspace_ids: Optional[List[int]] = None,
        file_id: Optional[int] = None,
    ):
        self._store = store
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._accessible_workspace_ids = accessible_workspace_ids
        self._file_id = file_id
        # Every passage the model was shown, keyed by (file_name, page), so the
        # answer can be turned into citations the customer can click. The model
        # reporting which page it used is not evidence that it read that page;
        # this is the record of what it was actually given.
        self.seen: Dict[tuple, Dict[str, Any]] = {}

    async def run(self, name: str, arguments: str | Dict[str, Any]) -> str:
        """Run one tool call and return its result as text for the model.

        Never raises. A tool that fails returns a sentence saying so, because a
        model that reads "no results" can recover by searching differently,
        while an exception ends the turn and the customer gets nothing.
        """
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError:
            return "Could not read the arguments for that call. Send valid JSON."

        try:
            if name == "search_documents":
                return await self._search(str(args.get("query") or "").strip())
            if name == "read_page":
                return await self._read_page(
                    str(args.get("file_name") or "").strip(), args.get("page")
                )
            if name == "list_documents":
                return await self._list_documents()
        except Exception as e:
            logger.error(f"tool {name} failed: {e}", exc_info=True)
            return f"The {name} tool failed. Try a different approach."

        return f"There is no tool called {name}."

    async def _search(self, query: str) -> str:
        if not query:
            return "search_documents needs a query."

        from api.services.llm_service import get_text_embedding

        chunks = await self._store.file_repo.hybrid_search(
            user_id=self._user_id,
            query=query,
            query_embedding=get_text_embedding(query),
            workspace_id=self._workspace_id,
            file_id=self._file_id,
            top_k=MAX_SEARCH_RESULTS,
            accessible_workspace_ids=self._accessible_workspace_ids,
        )
        if not chunks:
            return "No passages matched that query."

        lines = []
        for c in chunks:
            file_name = c.get("file_name") or "unknown"
            page = c.get("page_number")
            self._remember(file_name, page, c.get("file_url"))
            lines.append(
                f"{_cite_header(file_name, page)}\n{(c.get('content') or '').strip()}"
            )
        return "\n\n".join(lines)

    async def _read_page(self, file_name: str, page: Any) -> str:
        if not file_name:
            return "read_page needs a file_name."
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            return "read_page needs a whole number for page."

        listing = await self._files()
        match = next(
            (f for f in listing if (f.get("file_name") or "").lower() == file_name.lower()),
            None,
        )
        if not match:
            available = ", ".join(sorted({f.get("file_name") or "" for f in listing})) or "none"
            return f"There is no document called {file_name}. Available: {available}"

        segments = await self._store.file_repo.get_segments_for_page(match["id"], page_num)
        if not segments:
            return f"{file_name} has no page {page_num}."

        self._remember(match.get("file_name"), page_num, match.get("file_url"))
        body = "\n".join((s.get("content") or "").strip() for s in segments)
        header = _cite_header(match.get("file_name"), page_num)
        return f"{header}\n{body[:MAX_PAGE_CHARS]}"

    async def _list_documents(self) -> str:
        listing = await self._files()
        if not listing:
            return "There are no documents in this workspace."
        return "\n".join(f"- {f.get('file_name')}" for f in listing)

    async def _files(self) -> List[Dict[str, Any]]:
        result = await self._store.file_repo.get_files_for_user(
            user_id=self._user_id,
            skip=0,
            limit=100,
            workspace_id=self._workspace_id,
            accessible_workspace_ids=self._accessible_workspace_ids,
        )
        if isinstance(result, dict):
            return result.get("files") or result.get("items") or []
        return result or []

    def _remember(self, file_name: Optional[str], page: Any, file_url: Optional[str]) -> None:
        if not file_name or page is None:
            return
        self.seen.setdefault(
            (file_name, int(page)),
            {"file_name": file_name, "page_number": int(page), "file_url": file_url},
        )
