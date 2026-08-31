"""MCP: the knowledge base, as a tool somebody else's Claude can call.

WHAT THIS IS

One JSON-RPC endpoint speaking the Model Context Protocol over HTTP. A customer
connects their own Claude to it, asks a question there, and Claude reaches into
their workspace for the answer instead of guessing.

It adds no capability. `hybrid_search` and the tenant scoping already exist, and
this is a second door onto them for a caller that is not a browser.

RETRIEVAL, NOT ANSWERS

The tools hand back passages and citations. They deliberately do not run our own
answer generation, for two reasons and the first is enough on its own:

  - Latency. Search returns in well under a second. Answering goes through the
    worker and takes seconds, and a tool call that stalls for ten of them is a
    broken experience in somebody else's chat window.
  - The reasoning is the calling model's job. Wrapping it in ours would put two
    models in series to answer one question.

WHY THE TOOL DESCRIPTIONS ARE LONG

They are the only instructions we get to give a model we do not run. Whoever
connects this is not going to write a prompt explaining when to search, so the
descriptions have to carry it, including what to do with a passage read off a
figure that nothing could verify. That warning reaches a person through our own
answer text; here it has to reach a model that has never seen our UI.

SCOPE

Read only, and narrow on purpose. Two tools: find passages, and read one page in
full. Nothing writes, nothing lists other people's workspaces, and the
credential's ceiling is applied through `accessible_workspace_ids` exactly as it
is for search.
"""
from typing import Any, Dict, List, Optional
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from ..core.auth import Principal, authenticate_api_caller, get_store
from ..core.limits import assert_can_ask
from ..core.permissions import Capability
from ..core.rate_limit import limiter, CHAT_RATE_LIMIT
from ..core.urls import public_app_url
from ..repositories.repository_manager import RepositoryManager
from ..services.llm_service import get_text_embedding

logger = logging.getLogger(__name__)

mcp_router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

# The revision of the protocol this speaks. Sent back on initialize so a client
# that speaks a different one can say so rather than failing later on a shape it
# did not expect.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "syntextai"

RETRIEVE_CHUNKS = 40
MAX_PAGES = 10
SNIPPET_CHARS = 600

# JSON-RPC 2.0. Only the codes this endpoint can actually produce.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_knowledge",
        "description": (
            "Search this organization's own documents and return the passages "
            "that match, each with the document name and page number it came "
            "from. Use this whenever the question is about their business: "
            "their policies, procedures, contracts, manuals, pricing, clients, "
            "or anything a general model could not know. Prefer it over "
            "answering from memory. Cite the document and page in your reply, "
            "and include the passage's `open:` link so the reader can go and "
            "see the page for themselves: what you get here is text extracted "
            "from the document, and the page itself is what settles whether a "
            "number is right. If a passage is marked READ FROM "
            "A FIGURE, the text layer could not confirm it: say so plainly in "
            "your answer and tell the reader to check that page before acting "
            "on any number in it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in the words the document would use. "
                        "Search matches passages, so a phrase from the policy "
                        "finds more than a question about it."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_page",
        "description": (
            "Read one whole page of one document, given the file_id and "
            "page_number from a search_knowledge result. Use it when a passage "
            "is cut off mid-clause, when a table or a list continues past the "
            "snippet, or when the answer depends on wording you should quote "
            "exactly rather than paraphrase."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "integer",
                    "description": "From a search_knowledge result.",
                },
                "page_number": {
                    "type": "integer",
                    "description": "From a search_knowledge result.",
                },
            },
            "required": ["file_id", "page_number"],
        },
    },
    {
        "name": "list_drafts",
        "description": (
            "List the documents SyntextAI has already drafted for this "
            "workspace: the title of each, whether it is still a draft or has "
            "been approved into the knowledge base, and when it was written. "
            "Check this before writing a policy, procedure or SOP from "
            "scratch, because the business may already have one and rewriting "
            "it creates a second version nobody asked for. Titles and status "
            "only; to read what a draft says, open it in SyntextAI. Note that "
            "a draft is NOT part of the knowledge base and search_knowledge "
            "will not find it: it becomes searchable only after a person "
            "approves it, which is deliberate."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result(request_id: Any, payload: Dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": payload})


def _error(request_id: Any, code: int, message: str) -> JSONResponse:
    # Always HTTP 200 with an error member. JSON-RPC carries its own failure,
    # and a client reading the HTTP status instead sees a transport problem
    # where there is an application answer.
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _tool_text(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _page_link(file_id: Any, page_number: Any) -> str:
    """The app address that opens this document at this page.

    The hash is not decoration: the frontend is a HashRouter, so "/#/doc/59/62"
    is the path and "/doc/59/62" is a 404 from nginx.

    This is a reference, not a grant. Following it lands on the app's own route,
    which signs the reader in and mints the storage URL per request after the
    workspace check, exactly as a citation click does. Putting the signed URL
    here instead would have leaked a credential-free link to a private document
    into a transcript, and it would have died thirty minutes later.

    Nothing about this is specific to MCP. Any surface that carries a citation
    as text rather than as a click -- Teams, WhatsApp, an emailed answer -- wants
    this same URL, which is why the route is /doc and not /mcp/doc.
    """
    base = public_app_url()
    if page_number is None:
        return f"{base}/#/doc/{file_id}"
    return f"{base}/#/doc/{file_id}/{page_number}"


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS]
    space = cut.rfind(" ")
    if space > SNIPPET_CHARS // 2:
        cut = cut[:space]
    return cut + "…"


async def _reachable_workspaces(
    principal: Principal, store: RepositoryManager
) -> List[int]:
    """Live access, then the credential's ceiling. The same order as search.

    Called once per request at the endpoint rather than inside each tool. Two
    reasons, and the second is the one that matters: a tool that resolves its
    own tenant is a tool that can be written without doing so, and the boundary
    should be crossed at the door where it is obvious.
    """
    accessible = await store.workspace_repo.accessible_workspace_ids(principal.user_id)
    return principal.limit_workspaces(accessible)


async def _search_knowledge(
    principal: Principal,
    store: RepositoryManager,
    reachable: List[int],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    query = (arguments.get("query") or "").strip()
    if not query:
        return _tool_text("Ask for something to search for.", is_error=True)

    if not reachable:
        # Not an error. The credential is valid and reaches nothing, which is
        # what a revoked person's key looks like, and the model should say so
        # rather than retry.
        return _tool_text(
            "This connection no longer has access to any workspace. "
            "Ask the workspace owner to check it in Settings under Connections."
        )

    # Costs an embedding call, so the plan gate applies here exactly as it does
    # to asking a question in the product.
    await assert_can_ask(store, principal.user_id, workspace_id=reachable[0])

    # Never the query itself: a search box holds exactly the kind of thing a
    # customer would not want in our logs.
    logger.info("mcp search len=%s workspaces=%s", len(query), len(reachable))

    embedding = await get_text_embedding(query)
    chunks = await store.file_repo.hybrid_search(
        user_id=principal.user_id,
        query=query,
        query_embedding=embedding,
        top_k=RETRIEVE_CHUNKS,
        accessible_workspace_ids=reachable,
    )

    pages: Dict[Any, Dict[str, Any]] = {}
    for chunk in chunks or []:
        key = (chunk.get("file_id"), chunk.get("page_number"))
        unverified = bool((chunk.get("meta_data") or {}).get("vision_unverified_page"))
        existing = pages.get(key)
        if existing is None:
            pages[key] = {
                "file_id": chunk.get("file_id"),
                "file_name": chunk.get("file_name") or "Untitled",
                "page_number": chunk.get("page_number"),
                "text": _snippet(chunk.get("content") or ""),
                "vision_caution": unverified,
            }
        else:
            existing["vision_caution"] = existing["vision_caution"] or unverified

    found = list(pages.values())[:MAX_PAGES]
    if not found:
        return _tool_text(
            "Nothing in their documents matches that. Say so rather than "
            "answering from general knowledge, and suggest what they could "
            "upload if it should have been there."
        )

    lines: List[str] = []
    for page in found:
        where = page["file_name"]
        if page["page_number"] is not None:
            where += f", page {page['page_number']}"
        # The way back to the document itself. get_page returns the text we
        # extracted, which is the right thing for the model to read and the
        # wrong thing to settle an argument with: where extraction lost a
        # table's shape, the printed page still has it. So the passage carries
        # an address a person can open, and the reader checks the original.
        link = _page_link(page["file_id"], page["page_number"])
        # The caution is written into the passage itself rather than a field
        # beside it, because a field is easy for a model to skip and this is the
        # one that turns a torque value into a safety claim.
        caution = (
            " [READ FROM A FIGURE. The text layer could not confirm this. Tell "
            "the reader it came off a figure and should be checked against the "
            "document before they act on it.]"
            if page["vision_caution"]
            else ""
        )
        lines.append(
            f"--- {where} (file_id={page['file_id']}, "
            f"page_number={page['page_number']}) open: {link}{caution} "
            f"---\n{page['text']}"
        )

    return _tool_text("\n\n".join(lines))


async def _get_page(
    principal: Principal,
    store: RepositoryManager,
    reachable: List[int],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        file_id = int(arguments.get("file_id"))
        page_number = int(arguments.get("page_number"))
    except (TypeError, ValueError):
        return _tool_text(
            "file_id and page_number both have to be numbers from a "
            "search_knowledge result.",
            is_error=True,
        )

    record = await store.file_repo.get_file_by_id(file_id)
    # Authorized by the document's workspace, not by who uploaded it, and the
    # same refusal a stranger gets for a document that does not exist.
    if not record or record.get("workspace_id") not in reachable:
        return _tool_text("No document with that file_id is available.", is_error=True)

    pages = await store.file_repo.get_file_pages(file_id)
    for page in pages:
        if page.get("page_number") == page_number:
            name = record.get("file_name") or "Untitled"
            note = ""
            if record.get("superseded_by_id") is not None:
                # get_page names a document explicitly, so retrieval's
                # superseded filter does not apply. Say it rather than serve a
                # replaced policy as though it were current.
                note = (
                    "\n\n[This document has been replaced by a newer version. "
                    "Tell the reader before quoting it.]"
                )
            link = _page_link(file_id, page_number)
            return _tool_text(
                f"--- {name}, page {page_number} open: {link} ---"
                f"\n{page.get('content') or ''}{note}"
            )

    return _tool_text(
        f"That document has no page {page_number}.", is_error=True
    )


async def _list_drafts(
    principal: Principal,
    store: RepositoryManager,
    reachable: List[int],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """What has already been written, so it is not written twice.

    Read only and metadata only: titles, status, dates. No content and no
    prompt, which is what list_for_workspace already omits and for the same
    reason. Somebody wanting to read a draft opens it in the product, where the
    approval decision lives.

    Deliberately not an answer to "what do we say about X". A draft is not part
    of the knowledge base and search_knowledge cannot reach it, because a
    generated document that could be retrieved would make the model's output
    the model's source. This tool exists so the model knows a draft is there,
    not so it can quote from it.

    No embedding call, so no plan gate: this is a database read.
    """
    if not reachable:
        return _tool_text(
            "This connection no longer has access to any workspace. "
            "Ask the workspace owner to check it in Settings under Connections."
        )

    rows: List[Dict[str, Any]] = []
    for workspace_id in reachable:
        try:
            page = await store.draft_repo.list_for_workspace(workspace_id, limit=50)
        except Exception:
            logger.warning("Could not list drafts for a workspace", exc_info=True)
            continue
        rows.extend(page.get("items") or [])

    if not rows:
        return _tool_text(
            "SyntextAI has not drafted any documents for this workspace yet."
        )

    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)

    lines: List[str] = []
    for row in rows[:50]:
        title = row.get("title") or "Untitled"
        approved = row.get("ingested_file_id") is not None
        state = (
            "approved, and now part of the knowledge base"
            if approved
            else f"{row.get('status') or 'draft'}, not yet in the knowledge base"
        )
        when = str(row.get("created_at") or "")[:10]
        lines.append(f"- {title} ({state}{', ' + when if when else ''})")

    return _tool_text(
        "Documents SyntextAI has drafted for this workspace:\n"
        + "\n".join(lines)
        + "\n\nOnly the approved ones are searchable. To read a draft, or to "
        "approve one, open SyntextAI."
    )


HANDLERS = {
    "search_knowledge": _search_knowledge,
    "get_page": _get_page,
    "list_drafts": _list_drafts,
}


@mcp_router.post("")
@limiter.limit(CHAT_RATE_LIMIT)
async def mcp_endpoint(
    request: Request,
    principal: Principal = Depends(authenticate_api_caller),
    store: RepositoryManager = Depends(get_store),
):
    """One JSON-RPC method per request, over the credential that carried it."""
    try:
        message = await request.json()
    except Exception:
        return _error(None, PARSE_ERROR, "Could not parse that as JSON.")

    if not isinstance(message, dict):
        # Batches are not supported. Every client sends one message per request
        # in practice, and accepting a list would mean a partial failure with no
        # obvious answer about what the credential spent.
        return _error(None, INVALID_REQUEST, "Send one JSON-RPC object per request.")

    method = message.get("method")
    request_id = message.get("id")

    # A notification has no id and expects no body. `initialized` is the only
    # one that arrives here, and answering it with a result is what makes a
    # client hang waiting for a response it did not ask for.
    if request_id is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            },
        )

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, INVALID_PARAMS, f"No tool called {name!r}.")
        # The credential's own limit, checked before any workspace is looked up.
        # Both tools read, so a credential that cannot read cannot call either.
        if not principal.permits(Capability.READ):
            return _result(
                request_id,
                _tool_text("This connection is not allowed to read.", is_error=True),
            )
        # The tenant boundary, crossed once, here, where it is visible to
        # anyone reading this handler and to test_every_route_is_scoped.py.
        reachable = await _reachable_workspaces(principal, store)
        try:
            payload = await handler(
                principal, store, reachable, params.get("arguments") or {}
            )
        except Exception:
            # A tool failure is a result the model can react to, not a transport
            # error. Nothing about the exception reaches the caller: the same
            # rule the rest of the API follows about exception text.
            logger.error("mcp tool %s failed", name, exc_info=True)
            return _result(
                request_id,
                _tool_text("That failed on our side. Try again.", is_error=True),
            )
        return _result(request_id, payload)

    return _error(request_id, METHOD_NOT_FOUND, f"Unsupported method {method!r}.")
