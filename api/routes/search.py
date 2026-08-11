"""Finding a passage, as opposed to being told an answer.

WHY THIS EXISTS SEPARATELY FROM CHAT

The site has sold "AI search" as its own capability since before there was any
such thing in the code: it was the same retrieval engine chat uses, described
twice. This closes that gap by building the surface rather than deleting the
claim, because search and chat are genuinely different products to the person
using them.

Chat is for "what does our policy say", where somebody wants the answer. Search
is for "where is that clause", where they want the document and will read it
themselves. Somebody who does not trust a generated answer yet will use this
every day and never open chat, which is the argument for having it in a
compliance-sensitive vertical.

WHAT THIS IS, MECHANICALLY

Chat with the expensive half removed. Embed the question once, run the same
`hybrid_search` the answer path runs, and return the rows. No query rewriting,
no term expansion, no model call, no message saved, no queued job. That is why
it can be an ordinary request answering in well under a second while chat goes
through the worker.

Four things stop it being a literal passthrough of the retrieval list, and all
four are in here:

1. **Chunks are not results.** A chunk is a slice of a page, so twenty rows can
   be five near-identical snippets from page 12 of one file. They are grouped
   back into the page, which is the unit a person opens and the unit a citation
   names.
2. **The score never leaves this module.** It is a reciprocal rank fusion
   number on an arbitrary scale, not a percentage match. It orders the list and
   is not in the response.
3. **The same scoping as chat.** A new route that reads documents is exactly
   where the forgotten-WHERE class of bug has bitten before, so the workspace
   rules are the ones the query agent uses, not new ones written here.
4. **Paid for, and rate limited.** This calls the embedding service, which
   costs money per call, so it needs both checks chat has. Without them it is a
   free door standing next to a locked one.

NO QUERY REWRITING, ON PURPOSE

Chat rewrites the question because a rewritten question retrieves better before
a model reads the results. Somebody typing "termination clause" into a search
box means those words, and silently searching for something else is the fastest
way to make search feel broken.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from ..core.auth import authenticate_user, get_store
from ..core.limits import assert_can_ask
from ..core.log_safety import safe_text
from ..core.rate_limit import limiter, CHAT_RATE_LIMIT
from ..repositories.repository_manager import RepositoryManager
from ..services.llm_service import get_text_embedding

import logging

logger = logging.getLogger(__name__)

search_router = APIRouter(prefix="/api/v1/search", tags=["search"])

# Chunks to retrieve before grouping. Higher than the number of results shown,
# because several chunks routinely collapse into one page and a list that asked
# for ten pages would otherwise return four.
RETRIEVE_CHUNKS = 40

# Pages returned. A search result list is scrolled, not read whole, so this is
# generous where chat's context budget cannot be.
MAX_PAGES = 20

# Enough to recognise the passage, not so much that the list becomes the
# document. The viewer is one click away and shows the whole page.
SNIPPET_CHARS = 320


class SearchResult(BaseModel):
    file_id: int
    file_name: str
    page_number: Optional[int] = None
    snippet: str
    # How many separate passages on this page matched. Countable and honest,
    # unlike a relevance percentage, and it is why one page ranks above another
    # in a way a person can check by opening it.
    passages: int


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]


def _snippet(text: str) -> str:
    """A readable fragment, cut at a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS]
    space = cut.rfind(" ")
    if space > SNIPPET_CHARS // 2:
        cut = cut[:space]
    return cut + "…"


@search_router.get("", response_model=SearchResponse)
@limiter.limit(CHAT_RATE_LIMIT)
async def search_documents(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500, description="What to find"),
    workspace_id: Optional[int] = Query(None, description="Workspace to search"),
    file_id: Optional[int] = Query(None, description="Restrict to one document"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
) -> SearchResponse:
    """Passages matching `q`, grouped into the pages they came from."""
    user_id = user_data["user_id"]
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Type something to search for.",
        )

    # Identical to what asking a question does, deliberately, and copied from
    # `messages.py` rather than reasoned out again here. Searching a workspace
    # and asking a question of a workspace are the same act with a different
    # ending, so any difference between these two blocks is a bug waiting to be
    # found by whichever one is looser.
    #
    # Three things were different in the first version of this route, and all
    # three are the kind that survive review because each looks defensible
    # alone:
    #
    #   - It answered 403 where chat answers 404. A 403 confirms the workspace
    #     exists and says you may not have it; a 404 says nothing at all. For a
    #     resource named by an id in a URL, that difference is the whole
    #     question of whether a stranger can enumerate what a company owns.
    #   - It asked "may you do it" (`assert_workspace_capability`) for what is a
    #     read. The rule in this codebase is that *may you see it* is
    #     `accessible_workspace_ids`. The two agree today, which is exactly why
    #     having both answer one question is how they stop agreeing later.
    #   - It let a workspace-less document through on its uploader. Chat has no
    #     such branch, and one extra rule in one of two places is the shape of
    #     every access bug this codebase has had.
    accessible = await store.workspace_repo.accessible_workspace_ids(user_id)

    if workspace_id is not None and workspace_id not in accessible:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if file_id is not None:
        record = await store.file_repo.get_file_by_id(file_id)
        # Authorized by the document's workspace, not by who uploaded it, or a
        # member cannot search a document their owner added.
        if not record or record.get("workspace_id") not in accessible:
            raise HTTPException(status_code=404, detail="File not found")

    # Scoped to the one workspace when given, and to everything this person can
    # reach when not, which is what the query agent does with the same values.
    accessible_ids = None if workspace_id is not None else accessible

    # Paid for. This runs an embedding call, so an unsubscribed organization
    # searching is the same free ride that asking used to be.
    await assert_can_ask(store, user_id, workspace_id=workspace_id)

    # Never the question itself: the same rule questions follow, because a
    # search box holds exactly the kind of thing a customer would not want in
    # our logs.
    logger.info("search len=%s workspace=%s", len(query), workspace_id)

    query_embedding = await get_text_embedding(query)
    chunks = await store.file_repo.hybrid_search(
        user_id=user_id,
        query=query,
        query_embedding=query_embedding,
        workspace_id=workspace_id,
        file_id=file_id,
        top_k=RETRIEVE_CHUNKS,
        accessible_workspace_ids=accessible_ids,
    )

    # Grouped into pages, keeping the order retrieval gave us. The first chunk
    # of a page is its best-scoring one, so the snippet shown is the passage
    # that actually matched, and the page's position is that chunk's position.
    pages: Dict[Any, Dict[str, Any]] = {}
    for chunk in chunks or []:
        key = (chunk.get("file_id"), chunk.get("page_number"))
        existing = pages.get(key)
        if existing is None:
            pages[key] = {
                "file_id": chunk.get("file_id"),
                "file_name": chunk.get("file_name") or "Untitled",
                "page_number": chunk.get("page_number"),
                "snippet": _snippet(chunk.get("content") or ""),
                "passages": 1,
            }
        else:
            existing["passages"] += 1

    results = [SearchResult(**page) for page in list(pages.values())[:MAX_PAGES]]
    logger.info(
        "search returned %s page(s) from %s chunk(s)", len(results), len(chunks or [])
    )
    return SearchResponse(query=query, results=results)
