"""Documents SyntextAI writes, and what has to be true before one can answer.

A draft is written by the model from the workspace's own documents and from the
conversation, and it is NOT part of the knowledge base. Nothing here can make it
one. Approving a draft is a separate, deliberate act that writes the bytes to
storage and creates an ordinary `files` row queued for ingestion, which is the
same path an upload takes.

That separation is the point of the feature, not a detail of it. If a generated
draft could be retrieved on its own, the model's output would become the model's
source of truth: it writes a plausible SOP with one wrong figure, that is
ingested, and afterwards it cites itself with a page reference that looks
exactly like a real one. Drafts live in their own table, retrieval joins `files`
and never joins that table, so a draft is unretrievable by construction rather
than by a flag somebody remembers to check.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field

from ..core.auth import authenticate_user, get_store
from ..core.limits import assert_can_create_doc
from ..core.permissions import Capability, assert_workspace_capability
from ..core.rate_limit import limiter, UPLOAD_RATE_LIMIT
from ..core.utils import upload_bytes_to_gcs
from ..repositories.repository_manager import RepositoryManager
from ..services.document_export import markdown_to_docx, markdown_to_pdf, safe_filename
from ..services.llm_service import gradient_chat

logger = logging.getLogger(__name__)

drafts_router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])

# How many retrieved pages a draft is written from. Higher than a chat answer's
# working set on purpose: a question wants the few pages that answer it, and a
# document wants the breadth of what the workspace says on the subject.
DRAFT_TOP_K = 30

# A document is several times longer than an answer, and this is NOT the chat
# path's budget.
#
# Drafting went through generate_explanation first, which hardcodes 1500
# completion tokens because that is right for an answer. Reasoning is spent from
# the SAME allowance as the output, so a two-page SOP with a table sometimes
# reasoned its way through the whole budget and returned an empty string: the
# request succeeded, `_has_content` rejected it, all three attempts failed, and
# the customer got "Could not write the document. Try again." on roughly one
# attempt in four. The same trap is recorded in llm_service beside
# reasoning_effort, where raising the answer path to medium silently broke query
# expansion.
DRAFT_MAX_TOKENS = int(os.getenv("DRAFT_MAX_TOKENS", "4000"))
# And spend that allowance writing rather than thinking. The grounding rules are
# explicit instructions to follow, not a problem to reason about.
DRAFT_REASONING_EFFORT = os.getenv("DRAFT_REASONING_EFFORT", "low").strip().lower()


class GenerateRequest(BaseModel):
    workspace_id: int
    prompt: str = Field(..., min_length=3, max_length=2000)
    # No title field. A draft names itself from its own opening heading, and the
    # document view renames it afterwards, so a third way to set it was an
    # argument nothing could pass.
    # Whether the conversation so far is part of what this is written from.
    # Off by default: most drafts are written from documents, and silently
    # folding in an unrelated chat is a surprising way to get a wrong document.
    history_id: Optional[int] = None


class UpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None


async def _authorized_draft(
    draft_id: int, user_id: int, store: RepositoryManager, capability: Capability
) -> Dict[str, Any]:
    """One draft, or a refusal.

    404 rather than 403 when naming a draft by id, so a stranger cannot discover
    which ids exist by reading the refusal. The workspace check is what keeps
    one company's drafts out of another's, and it is done here rather than in
    the repository so that no query can reach a draft without it.
    """
    draft = await store.draft_repo.get(draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    await assert_workspace_capability(store, user_id, draft["workspace_id"], capability)
    return draft


@drafts_router.post("/generate", status_code=status.HTTP_201_CREATED)
@limiter.limit(UPLOAD_RATE_LIMIT)
async def generate_draft(
    request: Request,
    body: GenerateRequest = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Write a document from this workspace's documents, and keep it out of the corpus.

    Rate limited like an upload rather than like a question. One call runs a
    retrieval and a generation over a wide context, so it costs meaningfully
    more than a chat turn and is a paid endpoint in the sense that matters.
    """
    user_id = user_data["user_id"]
    # Writing a document into a workspace is adding to it.
    await assert_workspace_capability(
        store, user_id, body.workspace_id, Capability.UPLOAD_DOCUMENT
    )
    await assert_can_create_doc(store, user_id, workspace_id=body.workspace_id)

    from ..services.llm_service import get_text_embedding

    embedding = await get_text_embedding(body.prompt)
    if not embedding:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not read the request. Try again.",
        )

    passages = await store.file_repo.hybrid_search(
        user_id=user_id,
        query=body.prompt,
        query_embedding=embedding,
        workspace_id=body.workspace_id,
        top_k=DRAFT_TOP_K,
    )
    if not passages:
        # The same refusal a question gets. Writing a document from nothing is
        # the failure this whole product is built against: it would be fluent,
        # confident and sourced from the model's training data, and the customer
        # would have no way to tell.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="There are no documents in this workspace that cover that. "
                   "Upload the source material first.",
        )

    history_text = ""
    if body.history_id is not None:
        # Authorized by the same workspace the draft is being written into, so
        # a history id from another tenant cannot be read through this.
        history_text = await _history_for_workspace(
            store, body.history_id, user_id, body.workspace_id
        )

    numbered, sources = _context_and_sources(passages)
    content = await gradient_chat(
        _draft_prompt(body.prompt, numbered, history_text),
        max_tokens=DRAFT_MAX_TOKENS,
        reasoning_effort=DRAFT_REASONING_EFFORT,
    )
    if not content or not content.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not write the document. Try again.",
        )

    draft = await store.draft_repo.create(
        workspace_id=body.workspace_id,
        created_by=user_id,
        # The document names itself better than its request does. "A hand
        # hygiene quick reference for new staff. Use a table for when to..." is
        # what somebody typed; "Hand Hygiene Quick Reference" is what they
        # wanted, and it is also the filename of the .docx they download.
        title=(_heading_of(content) or _title_from(body.prompt)),
        prompt=body.prompt,
        content=content.strip(),
        sources=sources,
    )
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the document",
        )
    logger.info(
        {"event": "draft.generated", "draft_id": draft["id"],
         "workspace_id": body.workspace_id, "passages": len(passages)}
    )
    return draft


@drafts_router.get("")
async def list_drafts(
    workspace_id: int = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    user_id = user_data["user_id"]
    await assert_workspace_capability(store, user_id, workspace_id, Capability.READ)
    result = await store.draft_repo.list_for_workspace(
        workspace_id, skip=(page - 1) * page_size, limit=page_size
    )
    return {**result, "page": page, "page_size": page_size}


@drafts_router.get("/{draft_id}")
async def get_draft(
    draft_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    return await _authorized_draft(draft_id, user_data["user_id"], store, Capability.READ)


@drafts_router.patch("/{draft_id}")
async def update_draft(
    draft_id: int = Path(...),
    body: UpdateRequest = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Edit a draft. Editing is why this is a document and not a chat message."""
    user_id = user_data["user_id"]
    fields = body.model_fields_set if body else set()
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update")

    await _authorized_draft(draft_id, user_id, store, Capability.UPLOAD_DOCUMENT)
    updated = await store.draft_repo.update(
        draft_id,
        title=body.title if "title" in fields else None,
        content=body.content if "content" in fields else None,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update the document",
        )
    return updated


# What each format needs, so adding a third is a row rather than a branch.
_EXPORT_FORMATS = {
    "docx": (
        markdown_to_docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Word document",
    ),
    "pdf": (markdown_to_pdf, "application/pdf", "PDF"),
}


@drafts_router.get("/{draft_id}/export")
async def export_draft(
    draft_id: int = Path(...),
    format: str = Query("docx", description="docx or pdf"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """The draft as a file: Word to edit further, PDF to hand out.

    Reading, so it is held to READ: somebody who may see a document may take a
    copy of it, and refusing that while showing them the text on screen would be
    theatre.

    Both formats carry the provenance note at the top, before the content. They
    leave the product and get emailed, printed and pinned to a wall, which is
    exactly when everyone forgets a machine drafted it.
    """
    chosen = (format or "docx").strip().lower()
    if chosen not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That format is not available. Choose docx or pdf.",
        )
    build, media_type, human_name = _EXPORT_FORMATS[chosen]

    draft = await _authorized_draft(draft_id, user_data["user_id"], store, Capability.READ)

    try:
        # Both writers are synchronous and a long document is real work, so this
        # goes to a thread rather than stalling the event loop for every other
        # request on the process.
        payload = await asyncio.to_thread(
            build,
            draft["content"],
            title=draft["title"],
            sources=draft.get("sources"),
        )
    except Exception as e:
        logger.error(f"Could not build {chosen} for draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not build the {human_name}",
        )

    filename = safe_filename(draft["title"], chosen)
    logger.info({
        "event": "draft.exported", "draft_id": draft_id,
        "format": chosen, "bytes": len(payload),
    })
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            # The filename is already stripped to characters that cannot break
            # out of the header value.
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@drafts_router.post("/{draft_id}/ingest", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(UPLOAD_RATE_LIMIT)
async def ingest_draft(
    request: Request,
    draft_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Approve a draft into the knowledge base, so it can answer questions.

    This is the only way a generated document ever becomes retrievable, and it
    takes a person. It creates an ordinary `files` row and queues the same
    ingestion an upload queues: an approval is another way in, not a way around,
    so a draft is held to the plan limit and the duplicate-name rule like
    anything else.
    """
    user_id = user_data["user_id"]
    draft = await _authorized_draft(draft_id, user_id, store, Capability.UPLOAD_DOCUMENT)

    if draft["status"] == "ingested" and draft["ingested_file_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That document is already in the knowledge base",
        )

    workspace_id = draft["workspace_id"]
    await assert_can_create_doc(store, user_id, workspace_id=workspace_id)

    filename = f"{draft['title']}.md"
    if await store.file_repo.file_name_exists_in_workspace(workspace_id, filename):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with that name is already in this workspace. Rename it first.",
        )

    payload = _markdown_with_provenance(draft).encode("utf-8")

    file_id = await store.file_repo.add_file(
        user_id=user_id,
        file_name=filename,
        file_url="",
        file_size_bytes=len(payload),
        workspace_id=workspace_id,
    )
    if not file_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not add the document",
        )

    gcs_url = await upload_bytes_to_gcs(payload, workspace_id, file_id, filename)
    if not gcs_url or not await store.file_repo.set_file_url(file_id, gcs_url):
        # Same rule as an upload: a file row with no stored object is a document
        # that appears in the list and can never be opened.
        await store.file_repo.delete_file_entry(file_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not store the document",
        )

    await store.agent_run_repo.enqueue_run(
        run_type="ingest_file",
        agent_name="IngestionAgent",
        agent_version=None,
        payload={
            "file_id": int(file_id),
            "user_id": int(user_id),
            "workspace_id": int(workspace_id),
            "filename": filename,
            "file_url": gcs_url,
            "language": "English",
            "comprehension_level": "beginner",
            "file_size_bytes": len(payload),
        },
        user_id=int(user_id),
        workspace_id=int(workspace_id),
        file_id=int(file_id),
        priority=200,
    )

    await store.draft_repo.mark_ingested(draft_id, file_id)
    logger.info(
        {"event": "draft.ingested", "draft_id": draft_id,
         "file_id": file_id, "workspace_id": workspace_id, "user_id": user_id}
    )
    return {"draft_id": draft_id, "file_id": file_id, "status": "ingested"}


@drafts_router.delete("/{draft_id}")
async def delete_draft(
    draft_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Delete the draft. Any document already approved from it stays.

    Approving produced a real document with its own life in the knowledge base,
    and throwing away the draft it came from should not silently remove it.
    """
    user_id = user_data["user_id"]
    await _authorized_draft(draft_id, user_id, store, Capability.DELETE_DOCUMENT)
    if not await store.draft_repo.delete(draft_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the document",
        )
    return {"draft_id": draft_id, "deleted": True}


# --- helpers -----------------------------------------------------------------

async def _history_for_workspace(
    store: RepositoryManager, history_id: int, user_id: int, workspace_id: int
) -> str:
    """The conversation, only if it belongs to the workspace being written into.

    Without this check a history id from another tenant would be read straight
    into a draft, which is that tenant's conversation leaving their company.
    """
    try:
        # accessible_workspace_ids is the repository's own tenant boundary, and
        # passing the single workspace being written into is tighter than a
        # check written here: a conversation from another workspace returns
        # nothing rather than being caught afterwards.
        messages = await store.chat_repo.get_messages_for_chat_history(
            history_id, user_id, accessible_workspace_ids=[int(workspace_id)]
        )
    except Exception as e:
        logger.warning(f"Could not read history {history_id}: {e}")
        return ""
    if not messages:
        return ""
    lines = []
    for m in messages[-20:]:
        sender = m.get("sender") or m.get("role") or "user"
        text = (m.get("content") or "").strip()
        if text:
            lines.append(f"{sender}: {text}")
    return "\n".join(lines)


def _context_and_sources(passages: List[Dict[str, Any]]):
    """Numbered segments for the model, and a provenance list for the customer."""
    numbered_parts = []
    sources = []
    for i, p in enumerate(passages, start=1):
        content = (p.get("content") or "").strip()
        if not content:
            continue
        numbered_parts.append(f"[Segment {i}]\n{content}")
        sources.append({
            "segment": i,
            "file_id": p.get("file_id"),
            "file_name": p.get("file_name"),
            "page_number": p.get("page_number"),
        })
    return "\n\n".join(numbered_parts), sources


def _draft_prompt(request_text: str, numbered_context: str, history_text: str) -> str:
    """The grounding contract first, the formatting after.

    Same order and the same reason as the chat prompt: retrieval returns its
    best pages because a search ranks, it does not judge, so the instruction not
    to fill gaps has to come before anything about headings. A document is more
    dangerous than an answer here, because it is longer, it looks official, and
    somebody will hand it to staff.
    """
    history_block = (
        f"\n\nCONVERSATION THIS WAS ASKED IN:\n{history_text}\n" if history_text else ""
    )
    return (
        "You are writing a document for a small business, using ONLY the "
        "numbered segments below, which come from that business's own "
        "documents.\n\n"
        "GROUNDING, BEFORE ANYTHING ELSE:\n"
        "Write only what the segments support. Do not add steps, figures, "
        "deadlines, legal requirements or best practices from your own "
        "knowledge, however standard they seem. If the segments do not cover "
        "part of what was asked for, write that section as a single line "
        "reading: TO BE COMPLETED: <what is missing>. A gap the reader can see "
        "is useful; a gap filled with something plausible is dangerous, because "
        "staff will follow it.\n\n"
        "NEVER REFER TO THE SEGMENTS IN THE DOCUMENT:\n"
        "The numbering is scaffolding for you, not content. Do not write "
        "\"[Segment 4]\", \"(Segment 9)\", \"per the segments\" or anything like "
        "them. This document is going to be printed and handed to staff who "
        "have never heard of a segment, and a procedure that cites one reads as "
        "broken.\n\n"
        "FORM:\n"
        "Write in Markdown. Open with a single # title, then short sections "
        "under ## headings. Use numbered lists for anything performed in order. "
        "Use the business's own terms, figures and dates exactly as the "
        "segments give them. Write plainly, for somebody doing the job, not for "
        "a manager reading about it. No preamble, no closing summary, no note "
        "about being an AI: output the document itself and nothing else.\n\n"
        f"WHAT TO WRITE:\n{request_text}\n"
        f"{history_block}\n"
        f"SEGMENTS:\n{numbered_context}\n"
    )


def _heading_of(content: str) -> str:
    """The document's own H1, if it opened with one.

    Only the first non-empty line is considered: a heading further down is a
    section, not the document's name.
    """
    for line in (content or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return stripped[2:].strip()[:200]
        return ""
    return ""


def _title_from(prompt: str) -> str:
    """A usable title when nobody gave one, rather than an LLM call for six words."""
    text = " ".join((prompt or "").split())
    for lead in ("write a ", "write an ", "write ", "create a ", "create an ",
                 "create ", "draft a ", "draft an ", "draft ", "generate a ",
                 "generate an ", "generate ", "make a ", "make an ", "make "):
        if text.lower().startswith(lead):
            text = text[len(lead):]
            break
    text = text.rstrip(" .?!")
    if len(text) > 80:
        text = text[:77].rsplit(" ", 1)[0] + "..."
    return (text[:1].upper() + text[1:]) if text else "Untitled document"


def _markdown_with_provenance(draft: Dict[str, Any]) -> str:
    """What actually gets stored when a draft is approved.

    The provenance note is part of the document, not metadata beside it. Once
    this is in the knowledge base it will be retrieved, cited and read by people
    who were not in the room when it was generated, and the one thing they need
    to know is that a person approved a machine's draft rather than writing it.
    """
    lines = [draft["content"].rstrip(), "", "---", ""]
    lines.append(
        "*Written by SyntextAI from this workspace's documents, and approved by "
        "a person before being added to the knowledge base.*"
    )
    sources = draft.get("sources") or []
    if sources:
        seen = []
        for s in sources:
            name = s.get("file_name")
            if name and name not in seen:
                seen.append(name)
        if seen:
            lines.append("")
            lines.append("*Drawn from: " + ", ".join(seen[:10]) + ".*")
    return "\n".join(lines) + "\n"
