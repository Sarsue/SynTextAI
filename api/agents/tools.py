"""The tools the agents search with. The model decides what to search; these
decide only what it is ALLOWED to see.

    list_documents()                     what is in the reader's workspaces
    search(query)                        hybrid search across all of it
    search_document(document_id, query)  the same search inside one document
    read_page(document_id, page)         a whole page, when a passage is cut short

Every tool is scoped by the same rule retrieval always used: the reader's
workspaces, or the one workspace or file the question was asked in. Ids come
from a model, so nothing here trusts one: an id outside the reader's scope
finds nothing, because the scope is in the SQL, not in a check up here.

WHAT THE MODEL SEES, AND WHAT TRAVELS BESIDE IT

Each tool returns (text, passages), LangChain's content-and-artifact form. The
text is what the model reads: every passage headed by an id like [c812]
(a chunk) or [p77] (a whole page), its document and its page. The passages are
the rows themselves, which the model never has to copy: when it names the ids
it wants, the rows are looked up from the artifacts, so a citation points at
exactly the row that was read.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from api.agents.progress import NO_PROGRESS, Progress
from api.services.llm_service import get_text_embedding

logger = logging.getLogger(__name__)

# Passages per search. Small, because the model reads every one of them and
# can always search again; the old fixed pipeline took 25 and could not. Was 8:
# every passage read is time spent on every later turn, and answers took 40 to
# 55s (2026-09-28), so fewer and shorter, traded for speed with Osas.
SEARCH_TOP_K = 5

# How much of each passage the model reads to judge it. The answer is written
# from the whole passage; this is only what the searcher sees. A page read with
# read_page is shown longer.
PASSAGE_CHARS = 900
PAGE_CHARS = 6000

# Documents list_documents shows.
LISTED_DOCUMENTS = 200


def passage_id(p: Dict[str, Any]) -> str:
    if p.get("pid"):
        return p["pid"]
    if p.get("chunk_id") is not None:
        return f"c{p['chunk_id']}"
    return f"p{p.get('segment_id')}"


def _show(p: Dict[str, Any], limit: int = PASSAGE_CHARS) -> str:
    where = p.get("file_name") or "document"
    if p.get("page_number") is not None:
        where += f", page {p['page_number']}"
    body = re.sub(r"[ \t]+", " ", (p.get("content") or "").strip())
    if len(body) > limit:
        body = body[:limit] + " …"
    return f"[{passage_id(p)}] {where} (document_id {p.get('file_id')})\n{body}"


def _listing(passages: List[Dict[str, Any]], empty: str, seen: Optional[set] = None) -> str:
    """The passages as the model reads them.

    A passage it has already read comes back as its id and one line, not its
    text again. Measured 2026-09-28 on the first real runs: gpt-oss-20b
    searched "car expenses recordkeeping" and its near-twins eight times, each
    reply repeating the same passages in full, about 12,000 characters a
    search. The context grew with every turn, every turn got slower, and
    nothing in the reply told it that it was going round in circles.
    """
    if not passages:
        return empty
    out, repeats = [], []
    for p in passages:
        pid = passage_id(p)
        if seen is not None and pid in seen:
            repeats.append(pid)
            continue
        if seen is not None:
            seen.add(pid)
        out.append(_show(p))
    if repeats:
        out.append("Already read above: " + ", ".join(f"[{r}]" for r in repeats) + ".")
    if repeats and len(out) == 1:
        out[0] = ("Nothing new: every passage this found was already read above ("
                  + ", ".join(f"[{r}]" for r in repeats) + "). Search for something "
                  "different, or decide.")
    return "\n\n".join(out)


# An identical search, asked again, is not run again. The model repeated
# "Ontario minimum wage" word for word on the first real runs (2026-09-28).
REPEATED = "You already ran exactly this search. Search for something different, or decide."


def _repeat(asked: set, where: Optional[int], query: str) -> bool:
    key = (where, " ".join(query.lower().split()))
    if key in asked:
        return True
    asked.add(key)
    return False


@dataclass
class Scope:
    """Who is asking, and what they may read, for one question."""
    store: Any
    user_id: int
    workspace_id: Optional[int] = None
    accessible_ids: Optional[List[int]] = None
    # Set when the question was asked about one file: every tool stays in it.
    file_id: Optional[int] = None

    async def search(self, query: str, file_id: Optional[int] = None) -> List[Dict[str, Any]]:
        # A scope that is one file stays one file, whatever id the model asks
        # for: a document worker is told its document, and cannot wander.
        if self.file_id is not None:
            file_id = self.file_id
        emb = await get_text_embedding(query)
        found = await self.store.file_repo.hybrid_search(
            user_id=self.user_id,
            query=query,
            query_embedding=emb,
            workspace_id=self.workspace_id,
            file_id=file_id,
            top_k=SEARCH_TOP_K,
            accessible_workspace_ids=self.accessible_ids,
        ) or []
        out = []
        for p in found:
            p = dict(p)
            p["pid"] = passage_id(p)
            out.append(p)
        return out

    async def documents(self) -> List[Dict[str, Any]]:
        listed = await self.store.file_repo.get_files_for_user(
            self.user_id, skip=0, limit=LISTED_DOCUMENTS,
            workspace_id=self.workspace_id, accessible_workspace_ids=self.accessible_ids,
        )
        docs = [d for d in (listed or {}).get("items", []) if not d.get("superseded_by_id")]
        if self.file_id is not None:
            docs = [d for d in docs if d.get("id") == self.file_id]
        return docs

    async def page(self, file_id: int, page_number: int) -> Optional[Dict[str, Any]]:
        if self.file_id is not None and file_id != self.file_id:
            return None
        p = await self.store.file_repo.read_page_for(
            user_id=self.user_id, file_id=file_id, page_number=page_number,
            workspace_id=self.workspace_id, accessible_workspace_ids=self.accessible_ids,
        )
        if p is not None:
            p["pid"] = f"p{p['segment_id']}"
        return p


def research_tools(scope: Scope, progress: Progress = NO_PROGRESS) -> List[BaseTool]:
    """Everything the researcher may do: the whole workspace."""
    # Passages this agent has already read, shared by all its tools.
    seen: set = set()
    asked: set = set()

    @tool
    async def list_documents() -> str:
        """List the documents you can search, with their document_id."""
        docs = await scope.documents()
        if not docs:
            return "There are no documents."
        return "\n".join(f"document_id {d['id']}: {d.get('file_name')}" for d in docs)

    @tool(response_format="content_and_artifact")
    async def search(query: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Search every document for passages about `query`. Write the query
        the way the document would phrase it; search again with different
        words if the results do not answer."""
        if _repeat(asked, None, query):
            return REPEATED, []
        progress.stage("searching", query=query)
        found = await scope.search(query)
        return _listing(found, "Nothing found. Try other words.", seen), found

    return [list_documents, search, *document_tools(scope, progress, seen=seen)]


def document_tools(scope: Scope, progress: Progress = NO_PROGRESS,
                   names: Optional[Dict[int, str]] = None,
                   seen: Optional[set] = None) -> List[BaseTool]:
    """Searching inside one document, and reading whole pages."""
    seen = set() if seen is None else seen
    asked: set = set()

    @tool(response_format="content_and_artifact")
    async def search_document(document_id: int, query: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Search inside one document for passages about `query`."""
        if _repeat(asked, int(document_id), query):
            return REPEATED, []
        found = await scope.search(query, file_id=int(document_id))
        name = (names or {}).get(int(document_id)) or (found[0].get("file_name") if found else None)
        progress.stage("reading", document=name or "a document", query=query)
        return _listing(found, "Nothing found in that document. Try other words.", seen), found

    @tool(response_format="content_and_artifact")
    async def read_page(document_id: int, page_number: int) -> Tuple[str, List[Dict[str, Any]]]:
        """Read one whole page of a document, for when a passage is cut off or
        a table continues past it."""
        p = await scope.page(int(document_id), int(page_number))
        if p is None:
            return "That page was not found.", []
        return _show(p, PAGE_CHARS), [p]

    return [search_document, read_page]


def found_passages(messages: Iterable[BaseMessage]) -> Dict[str, Dict[str, Any]]:
    """Every passage any tool returned during a run, by id, first seen first."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in messages:
        if isinstance(m, ToolMessage) and isinstance(m.artifact, list):
            for p in m.artifact:
                if isinstance(p, dict):
                    out.setdefault(passage_id(p), p)
    return out


def searches_made(messages: Iterable[BaseMessage]) -> List[str]:
    """The queries the model chose, in order. For the run record."""
    out: List[str] = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            q = (call.get("args") or {}).get("query")
            if q:
                out.append(str(q))
    return out
