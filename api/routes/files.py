import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile, Request, Response, status, Query, Path
from typing import List, Dict, Optional, Any, TypeVar
from sqlalchemy.orm import Session
from redis.exceptions import RedisError
from ..core import query_cache
from ..core.utils import (
    get_user_id,
    upload_to_gcs,
    upload_bytes_to_gcs,
    delete_from_gcs,
    generate_signed_url,
    move_object_to_workspace,
    SIGNED_URL_TTL,
)
import logging
import asyncio

# PRODUCTION: File size limits to prevent OOM and abuse
MAX_FILE_SIZE_MB = 100  # 100MB max for PDFs
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
from ..repositories.repository_manager import RepositoryManager
from fastapi.responses import JSONResponse
from ..core.limits import assert_can_create_doc
from ..core.permissions import Capability, assert_workspace_capability
from ..core.rate_limit import limiter, UPLOAD_RATE_LIMIT
from pydantic import BaseModel, Field
from ..models import File
from ..core.auth import authenticate_user, get_store
from ..services.connectors import ImportRefused, get_connector

class FileResponse(BaseModel):
    id: int
    file_name: str
    file_url: str
    created_at: Optional[datetime] = None
    user_id: Optional[int] = None
    file_type: Optional[str] = None
    processing_status: Optional[str] = None

    class Config:
        from_attributes = True

class UploadResponse(BaseModel):
    message: Optional[str] = None
    files: Optional[List[FileResponse]] = None
  

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Define a standardized API response model
T = TypeVar('T')

async def _may_see_file(file_record, user_id: int, store: RepositoryManager) -> bool:
    """Whether this caller may see this document.

    By WORKSPACE, never by uploader. The two status endpoints below checked
    `file_record["user_id"] != caller`, which is the same mistake this codebase
    already recorded fixing for retrieval and for the file list: documents belong
    to the workspace, not to whoever happened to add them, so checking the
    uploader refuses an invited staff member every document their owner
    uploaded.

    It did not leak anything. It was too strict, not too loose, and it broke
    quietly: a staff member's list polls for status, the batch endpoint silently
    omits every file the owner added, and those documents sit at "Processing" on
    their screen forever while the owner watches them go ready.

    Files with no workspace stay private to their uploader, which is how
    pre-workspace uploads behave everywhere else.
    """
    if not file_record:
        return False
    workspace_id = file_record.get("workspace_id")
    if workspace_id is None:
        return file_record.get("user_id") == user_id
    accessible = await store.workspace_repo.accessible_workspace_ids(user_id)
    return workspace_id in (accessible or [])


async def check_can_upload_to_workspace(workspace_id: int, user_id: int, store: RepositoryManager) -> None:
    """Raise 403 unless the caller may add documents to this workspace.

    Uploading was once unauthorized entirely: any authenticated user could pass
    an arbitrary workspace_id and upload into a workspace they were not a member
    of, since only file-level ownership was ever checked. Who may upload is now
    a row in the capability table rather than a role string compared here.
    """
    await assert_workspace_capability(store, user_id, workspace_id, Capability.UPLOAD_DOCUMENT)

async def check_can_read_workspace(workspace_id: int, user_id: int, store: RepositoryManager) -> None:
    """Raise 403 unless the caller may read this workspace.

    Reads are scoped by workspace rather than by uploader, so this is what keeps
    one organization's documents out of another's. Without it, passing an
    arbitrary workspace_id would return that workspace's files.
    """
    await assert_workspace_capability(store, user_id, workspace_id, Capability.READ)




class ImportRequest(BaseModel):
    """Documents a customer picked in Drive or SharePoint.

    The access token is theirs, minted in their browser by that provider's own
    picker for the documents they chose. It is used to fetch the bytes and is
    never written down: not to the database, not to a log line, not into an
    error message. See services/connectors.py for why there is no stored grant.
    """
    provider: str = Field(..., description="google_drive or sharepoint")
    access_token: str = Field(..., min_length=10)
    item_ids: List[str] = Field(..., min_length=1, max_length=25)


@files_router.post("/import", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(UPLOAD_RATE_LIMIT)
async def import_from_source(
    request: Request,
    payload: ImportRequest,
    workspace_id: int = Query(..., description="Workspace to import into"),
    language: str = Query(default="English"),
    comprehension_level: str = Query(default="Beginner"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Bring documents in from Drive or SharePoint.

    Every check an upload passes, this passes, in the same order and by calling
    the same functions: may you add documents to this workspace, is the company
    subscribed, is there already a document by that name here. An import that
    was allowed to skip any of them would be a way around the rules rather than
    another way in.

    Partial success is the honest outcome and is reported as such. Ten
    documents where one has been deleted in Drive since the customer picked it
    should import nine and say which one did not, rather than refusing all ten.
    """
    user_id = user_data["user_id"]

    await check_can_upload_to_workspace(workspace_id, user_id, store)

    try:
        connector = get_connector(payload.provider)
    except ImportRefused as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    imported: List[FileResponse] = []
    skipped: List[Dict[str, str]] = []

    for item_id in payload.item_ids:
        file_id = None
        gcs_url = None
        try:
            # Charged against the plan per document, like an upload, and
            # checked inside the loop so a customer at their limit imports what
            # fits rather than nothing at all.
            await assert_can_create_doc(store, user_id, workspace_id=workspace_id)

            document = await connector.fetch(item_id, payload.access_token)

            if await store.file_repo.file_name_exists_in_workspace(
                workspace_id, document.filename
            ):
                skipped.append({
                    "name": document.filename,
                    "reason": "A document with that name is already in this workspace.",
                })
                continue

            file_id = await store.file_repo.add_file(
                user_id=user_id,
                file_name=document.filename,
                file_url="",
                file_size_bytes=document.size,
                workspace_id=workspace_id,
            )
            if not file_id:
                skipped.append({"name": document.filename, "reason": "Could not be saved."})
                continue

            gcs_url = await upload_bytes_to_gcs(
                document.content, workspace_id, file_id, document.filename
            )
            if not gcs_url or not await store.file_repo.set_file_url(file_id, gcs_url):
                # Same rule as an upload: a file row with no stored object is a
                # document that appears in the list and can never be opened.
                await store.file_repo.delete_file_entry(file_id)
                skipped.append({"name": document.filename, "reason": "Could not be stored."})
                continue

            record = await store.file_repo.get_file_by_id(file_id=file_id)
            imported.append(FileResponse.model_validate(record))

            await store.agent_run_repo.enqueue_run(
                run_type="ingest_file",
                agent_name="IngestionAgent",
                agent_version=None,
                payload={
                    "file_id": int(file_id),
                    "user_id": int(user_id),
                    "workspace_id": int(workspace_id),
                    "filename": document.filename,
                    "file_url": gcs_url,
                    "language": language,
                    "comprehension_level": comprehension_level,
                    "file_size_bytes": int(document.size),
                },
                user_id=int(user_id),
                workspace_id=int(workspace_id),
                file_id=int(file_id),
                priority=200,
                max_attempts=3,
            )

        except HTTPException:
            # A refusal about the plan or the workspace is the whole request's
            # answer, not one document's.
            raise
        except ImportRefused as e:
            # Logged as well as returned. The browser shows the customer why,
            # but the first time an import came back empty the reason was only
            # visible by turning on httpx debug logging and reading a 404 out
            # of a request trace.
            logger.info(f"Import skipped in workspace {workspace_id}: {e}")
            skipped.append({"name": item_id, "reason": str(e)})
        except Exception as e:
            # Never the exception text: a provider error can carry a URL with
            # the access token in it.
            logger.error(f"Import failed for an item in workspace {workspace_id}: {type(e).__name__}")
            if file_id and not gcs_url:
                await store.file_repo.delete_file_entry(file_id)
            skipped.append({"name": item_id, "reason": "Could not be imported."})

    return {"imported": imported, "skipped": skipped}


# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
@limiter.limit(UPLOAD_RATE_LIMIT)
async def save_file(
    request: Request,
    language: str = Query(default="English"),
    comprehension_level: str = Query(default="Beginner"),
    workspace_id: Optional[int] = Query(None, description="Workspace ID to add file to"),
    files: Optional[List[UploadFile]] = FastAPIFile(None),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]

        content_type = request.headers.get("content-type", "")

        # --- Multipart File Upload ---
        if content_type.startswith("multipart/form-data"):
            if not files:
                raise HTTPException(status_code=400, detail="No files were uploaded.")

            # Resolve the target workspace once for the whole request (it's the
            # same workspace for every file in this upload) and gate on it before
            # touching any file, instead of re-resolving and re-checking per file.
            actual_workspace_id = workspace_id
            if not actual_workspace_id:
                # Fall back to a workspace they can actually see, not one they
                # own. An admin owns nothing, so the ownership list was empty
                # and the file was stored with no workspace at all — invisible
                # to everyone, including the owner of the organization it was
                # uploaded into, because visibility follows the workspace.
                accessible = await store.workspace_repo.list_accessible_workspaces(user_id)
                if accessible:
                    actual_workspace_id = accessible[0]["id"]
                    logger.info(f"Using default workspace {actual_workspace_id} for user {user_id}")

            if not actual_workspace_id:
                # Better to refuse than to store a document nobody can reach.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Choose a workspace to upload into.",
                )

            if actual_workspace_id:
                await check_can_upload_to_workspace(actual_workspace_id, user_id, store)

            uploaded_files_responses = []
            for file in files:
                # CRITICAL: Validate file size before processing
                file_content = await file.read()
                file_size = len(file_content)
                
                if file_size > MAX_FILE_SIZE_BYTES:
                    logger.warning(f"File {file.filename} rejected: {file_size / 1024 / 1024:.2f}MB exceeds {MAX_FILE_SIZE_MB}MB limit")
                    raise HTTPException(
                        status_code=413,  # Payload Too Large
                        detail=f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds maximum allowed size ({MAX_FILE_SIZE_MB}MB)"
                    )
                
                if file_size == 0:
                    logger.warning(f"File {file.filename} rejected: empty file")
                    raise HTTPException(
                        status_code=400,
                        detail=f"File {file.filename} is empty"
                    )
                
                logger.info(f"File {file.filename} validated: {file_size / 1024 / 1024:.2f}MB")

                # One name, one document, per workspace. Two files called
                # invoice.pdf sitting side by side in a shared folder is
                # confusing to everyone in it and makes citations ambiguous:
                # an answer says "invoice.pdf, page 3" and nobody knows which.
                if await store.file_repo.file_name_exists_in_workspace(
                    actual_workspace_id, file.filename
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"{file.filename} is already in this workspace. "
                            "Delete it first, or rename this one."
                        ),
                    )

                # The workspace's organization must be subscribed. Passing the
                # workspace is what makes the plan resolve against the company
                # that pays rather than the person uploading, so staff are
                # covered by their employer.
                await assert_can_create_doc(
                    store,
                    user_id,
                    workspace_id=actual_workspace_id,
                )

                # Reset file pointer after reading
                await file.seek(0)

                # The row comes first, because its id is what makes the stored
                # object unique inside a shared workspace folder. Two people
                # uploading the same filename used to write the same object and
                # silently overwrite each other.
                file_id = await store.file_repo.add_file(
                    user_id=user_id,
                    file_name=file.filename,
                    file_url="",
                    file_size_bytes=file_size,
                    workspace_id=actual_workspace_id
                )
                if not file_id:
                    logger.error(f"Failed to create file record for {file.filename}")
                    continue

                gcs_url = await upload_to_gcs(file, actual_workspace_id, file_id, file.filename)
                if not gcs_url:
                    # Take the row back out. A file row with no stored object is
                    # a document that shows up in the list and can never be
                    # opened or ingested.
                    logger.error(f"Failed to upload {file.filename} to storage (gcs_url is empty)")
                    await store.file_repo.delete_file_entry(file_id)
                    raise HTTPException(status_code=500, detail="Failed to upload file to storage")

                if not await store.file_repo.set_file_url(file_id, gcs_url):
                    logger.error(f"Failed to record stored location for {file.filename}")
                    await asyncio.to_thread(delete_from_gcs, gcs_url)
                    await store.file_repo.delete_file_entry(file_id)
                    raise HTTPException(status_code=500, detail="Failed to upload file to storage")

                file_record = await store.file_repo.get_file_by_id(file_id=file_id)
                uploaded_files_responses.append(FileResponse.model_validate(file_record))

                await store.agent_run_repo.enqueue_run(
                    run_type="ingest_file",
                    agent_name="IngestionAgent",
                    agent_version=None,
                    payload={
                        "file_id": int(file_id),
                        "user_id": int(user_id),
                        "workspace_id": int(actual_workspace_id) if actual_workspace_id is not None else None,
                        "filename": file.filename,
                        "file_url": gcs_url,
                        "language": language,
                        "comprehension_level": comprehension_level,
                        # Lets the worker classify this as a heavy or light
                        # ingest without an extra DB round trip.
                        "file_size_bytes": int(file_size),
                    },
                    user_id=int(user_id),
                    workspace_id=int(actual_workspace_id) if actual_workspace_id is not None else None,
                    file_id=int(file_id),
                    priority=200,
                    max_attempts=3,
                )

            return UploadResponse(
                message="File(s) uploaded successfully.",
                files=uploaded_files_responses
            )

        else:
            raise HTTPException(status_code=400, detail="Unsupported content type.")

    except RedisError as e:
        logger.error(f"Redis error in save_file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="A caching error occurred.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not save file")

# Route to retrieve files
@files_router.get("", response_class=JSONResponse)
async def retrieve_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    workspace_id: Optional[int] = Query(None, description="Filter by workspace ID"),
    organization_id: Optional[int] = Query(None, description="Active organization; scopes results to one tenant"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data['user_id']
        offset = (page - 1) * page_size

        # Files are visible by workspace, not by who uploaded them, so a
        # requested workspace must be authorized and an unscoped listing must be
        # limited to the workspaces this user can actually read.
        accessible_ids = None
        if workspace_id is not None:
            await check_can_read_workspace(workspace_id, user_id, store)
        else:
            accessible_ids = await store.workspace_repo.accessible_workspace_ids(
                user_id, organization_id=organization_id
            )

        paginated_result = await store.file_repo.get_files_for_user(
            user_id,
            skip=offset,
            limit=page_size,
            workspace_id=workspace_id,
            accessible_workspace_ids=accessible_ids,
        )
        
        db_files = paginated_result.get('items', [])
        total_files = paginated_result.get('total', 0)

        # Construct the response to match the frontend's expectation
        response_items = [
            {
                "id": f["id"],
                "file_name": f["file_name"],
                "file_url": f["file_url"],
                "created_at": f.get("created_at"),
                "file_type": f.get("file_type"),
                "status": f.get("processing_status", "uploaded"),
                # The list is the only place a customer can find out that a
                # document has been replaced. Retrieval already skips it, so
                # without this the row looks healthy and is silently never
                # cited, which is the confusion this feature exists to remove.
                "superseded_by_id": f.get("superseded_by_id"),
            }
            for f in db_files
        ]

        return {
            "items": response_items,
            "page": page,
            "page_size": page_size,
            "total": total_files,
        }
    except HTTPException:
        # A 403 from the workspace check is the answer, not a failure. Letting
        # the broad handler below swallow it turned "you do not have access to
        # this workspace" into "internal server error", which reads as the app
        # being broken rather than the request being refused — and hides a
        # denial from anyone reading logs or status codes.
        raise
    except Exception as e:
        logger.error(f"Error retrieving files for user {user_data.get('user_id')}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not retrieve files.")

def _status_to_progress(status: Optional[str]) -> int:
    """Map processing_status to a coarse progress percentage for polling UI."""
    mapping = {
        "uploaded": 0,
        "processing": 10,
        "extracting": 10,
        "embedding": 40,
        "storing": 70,
        "processed": 100,
        "failed": 0,
    }
    return mapping.get((status or "uploaded").lower(), 0)

@files_router.get("/{file_id}/status", response_class=JSONResponse)
async def get_file_status(
    file_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Return current processing status and derived progress for a single file."""
    try:
        file_record = await store.file_repo.get_file_by_id(file_id)
        if not await _may_see_file(file_record, user_data["user_id"], store):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        status_str = file_record.get("processing_status", "uploaded")
        return {
            "file_id": file_id,
            "processing_status": status_str,
            "progress": _status_to_progress(status_str),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting file status for {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not get file status")

@files_router.get("/{file_id}/access-url", response_class=JSONResponse)
async def get_file_access_url(
    file_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Mint a short-lived URL for reading one document.

    Documents are private in storage. files.file_url is a stable identity, not a
    fetchable link, which is what lets a saved answer's citations keep pointing
    at the right page years later without the link itself granting access to
    anyone who copies it. Authorization happens here, per request, against the
    document's workspace — so losing workspace access immediately stops working,
    rather than being enforced only by nobody knowing the URL.
    """
    file_record = await store.file_repo.get_file_by_id(file_id)
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    workspace_id = file_record.get("workspace_id")
    if workspace_id is not None:
        await check_can_read_workspace(workspace_id, user_data["user_id"], store)
    elif file_record.get("user_id") != user_data["user_id"]:
        # Pre-workspace rows fall back to the uploader.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    signed = generate_signed_url(file_record.get("file_url") or "")
    if not signed:
        raise HTTPException(status_code=502, detail="Could not open this document")

    return {
        "url": signed,
        "expires_in": int(SIGNED_URL_TTL.total_seconds()),
        # The four fields below are for a caller that has no file row to read
        # them off. Every caller inside the chat already holds the row from the
        # file list and ignores these; the /doc deep link arrives from outside
        # the app with nothing but an id, and the viewer needs a name to title
        # itself and a status before it decides whether to render anything.
        "file_name": file_record.get("file_name"),
        "file_type": file_record.get("file_type"),
        "status": file_record.get("processing_status") or "uploaded",
        "superseded_by_id": file_record.get("superseded_by_id"),
    }


@files_router.get("/{file_id}/content", response_class=JSONResponse)
async def get_file_content(
    file_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """The text we extracted, page by page, for documents a browser cannot render.

    A PDF opens in an iframe at #page=N and a citation lands where it points. A
    .docx cannot: no browser renders one, so the viewer fell through to
    "Unsupported file type" for a format the uploader accepts, the processors
    handle, and answers are cited from. The document was searchable and
    unreadable at the same time. .txt was broken the same way and nobody had
    reported it.

    Serving our own extraction rather than the original file is what makes a
    citation mean something here. The answer cites page 4; page 4 of a .docx is
    a section this pipeline defined, and only this pipeline can show it.

    Same authorization as access-url, per request against the document's
    workspace, because this returns document text and is exactly as sensitive
    as the file itself.
    """
    file_record = await store.file_repo.get_file_by_id(file_id)
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    workspace_id = file_record.get("workspace_id")
    if workspace_id is not None:
        await check_can_read_workspace(workspace_id, user_data["user_id"], store)
    elif file_record.get("user_id") != user_data["user_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    pages = await store.file_repo.get_file_pages(file_id)
    return {
        "file_name": file_record.get("file_name"),
        "pages": pages,
    }


@files_router.get("/status", response_class=JSONResponse)
async def get_files_status(
    ids: str = Query(..., description="Comma-separated file IDs"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Return status/progress for multiple files by IDs (batch polling)."""
    try:
        raw_ids = [s.strip() for s in ids.split(",") if s.strip()]
        results: List[Dict[str, Any]] = []
        # Resolved once. This endpoint is polled continuously while anything is
        # processing, so a per-file membership lookup would be one extra query
        # per file per poll, for an answer that cannot change inside the loop.
        accessible = set(
            await store.workspace_repo.accessible_workspace_ids(user_data["user_id"]) or []
        )
        for sid in raw_ids:
            try:
                fid = int(sid)
            except ValueError:
                continue
            file_record = await store.file_repo.get_file_by_id(file_id=fid)
            if not file_record:
                continue
            workspace_id = file_record.get("workspace_id")
            visible = (
                file_record.get("user_id") == user_data["user_id"]
                if workspace_id is None
                else workspace_id in accessible
            )
            if not visible:
                continue
            status_str = file_record.get("processing_status", "uploaded")
            results.append({
                "file_id": fid,
                "processing_status": status_str,
                "progress": _status_to_progress(status_str),
            })
        return {"items": results}
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error getting files status for ids={ids}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not get files status")

# Route to delete a file
@files_router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]

        file_to_delete = await store.file_repo.get_file_by_id(file_id)
        if not file_to_delete:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

        # Authorized by capability in the document's workspace, not by who
        # uploaded it. The uploader check refused an owner deleting a document
        # an admin had added, and left anything uploaded by a since-deleted
        # account permanently undeletable: those rows carry user_id NULL, which
        # equals nobody.
        workspace_id = file_to_delete.get('workspace_id')
        if workspace_id is not None:
            await assert_workspace_capability(
                store, user_id, workspace_id, Capability.DELETE_DOCUMENT
            )
        elif file_to_delete.get('user_id') != user_id:
            # Pre-workspace rows fall back to the uploader.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

        if file_to_delete.get('file_url') and "storage.googleapis.com" in file_to_delete.get('file_url'):
             await asyncio.to_thread(delete_from_gcs, file_to_delete.get('file_url'))

        if not await store.file_repo.delete_file_entry(file_id):
             raise HTTPException(status_code=500, detail="Failed to delete file entry.")

        # Answers cached from a set of documents that included this one must
        # not outlive it. Deleting a document and still being quoted it is the
        # worst version of a stale cache, because the reason it was deleted is
        # usually that it was wrong.
        await query_cache.bump_document_version(workspace_id)

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete file.")


# Move file to different workspace
class MoveFileRequest(BaseModel):
    workspace_id: int = Field(..., description="Target workspace ID")

class CurrencyRequest(BaseModel):
    """Which document replaced this one.

    The field accepts null, which is why the route reads `model_fields_set`
    rather than checking for None: clearing the link is how a customer says
    "this is current again", and that is a different request from one that never
    mentioned the field.
    """
    superseded_by_id: Optional[int] = Field(
        None, description="The document that replaced this one. Null clears it."
    )


@files_router.patch("/{file_id}/currency")
async def set_file_currency(
    file_id: int = Path(..., description="ID of the document to update"),
    body: CurrencyRequest = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Mark the document that replaced this one.

    A replaced document stops answering questions. That is the point: a
    workspace holding both the 2019 policy and the 2024 one cited whichever
    matched better, and the customer had no way to say which was true.

    Authorization is done on BOTH documents independently and never inferred
    from one to the other. The replacement id arrives from the browser and is
    not evidence of anything: without checking it, a member of one company
    could point their own document at a file id belonging to another and learn
    from the response whether that id exists.
    """
    user_id = user_data['user_id']
    fields = body.model_fields_set if body else set()
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to update",
        )

    file = await store.file_repo.get_file_by_id(file_id)
    # 404 rather than 403 when naming a resource by id, so a stranger cannot
    # discover which file ids exist by reading the refusal.
    if not file or file.get('workspace_id') is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    await assert_workspace_capability(
        store, user_id, file['workspace_id'], Capability.DELETE_DOCUMENT
    )

    superseded_by_id = None
    if 'superseded_by_id' in fields:
        superseded_by_id = body.superseded_by_id
        if superseded_by_id is not None:
            if int(superseded_by_id) == int(file_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A document cannot replace itself",
                )
            replacement = await store.file_repo.get_file_by_id(superseded_by_id)
            if not replacement:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
                )
            # Same workspace, both ends. Documents are read per workspace, so a
            # link across two of them would hide a document from people who can
            # see it, on the say-so of people who cannot.
            if replacement.get('workspace_id') != file['workspace_id']:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
                )
            await assert_workspace_capability(
                store, user_id, replacement['workspace_id'], Capability.READ
            )
            # A cycle would hide every document in it from retrieval with
            # nothing in the product able to explain why.
            if await store.file_repo.supersede_chain_reaches(superseded_by_id, file_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="That document is already replaced by this one",
                )

    ok = await store.file_repo.set_document_currency(
        file_id,
        superseded_by_id=superseded_by_id,
        set_superseded_by='superseded_by_id' in fields,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update the document",
        )

    # Answers already cached were computed with the old document still in
    # play, so they would keep citing it after the customer said not to.
    if 'superseded_by_id' in fields:
        await query_cache.bump_document_version(file['workspace_id'])

    logger.info(
        f"Currency set on file {file_id} by user {user_id}: "
        f"fields={sorted(fields)}"
    )
    updated = await store.file_repo.get_file_by_id(file_id)
    return {
        "file_id": file_id,
        "superseded_by_id": updated.get('superseded_by_id'),
    }


@files_router.patch("/{file_id}/workspace", status_code=status.HTTP_200_OK)
async def move_file_to_workspace(
    file_id: int = Path(..., description="ID of the file to move"),
    move_request: MoveFileRequest = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Move a file to a different workspace.
    
    Args:
        file_id: ID of the file to move
        move_request: Request body with target workspace_id
        user_data: Authenticated user data
        store: Repository manager
        
    Returns:
        Success message
        
    Raises:
        403: File or workspace doesn't belong to user
        404: File or workspace not found
    """
    try:
        user_id = user_data['user_id']
        target_workspace_id = move_request.workspace_id
        
        # Verify file exists and belongs to user
        file = await store.file_repo.get_file_by_id(file_id)
        if not file:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )
        
        # Authorized on both ends, by capability. It checked file['user_id']
        # against the caller, which refused an admin moving a document somebody
        # else had uploaded, and read the target from list_workspaces_for_user,
        # which answers what you own rather than what you may use.
        if file.get('workspace_id') is not None:
            await assert_workspace_capability(
                store, user_id, file['workspace_id'], Capability.DELETE_DOCUMENT
            )
        await assert_workspace_capability(
            store, user_id, target_workspace_id, Capability.UPLOAD_DOCUMENT
        )
        
        # Update file's workspace
        # Move the object first. A document's path names the workspace it is
        # in, and the whole layout rests on that: leaving the object behind
        # would mean deleting the old workspace destroyed a document belonging
        # to the new one. Failing here leaves everything as it was.
        moved_url = await asyncio.to_thread(
            move_object_to_workspace,
            file.get('file_url') or '',
            target_workspace_id,
        )
        if not moved_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not move the stored document",
            )

        success = await store.file_repo.update_file_workspace(
            file_id, target_workspace_id, file_url=moved_url
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to move file"
            )
        
        logger.info(f"File {file_id} moved to workspace {target_workspace_id} by user {user_id}")
        
        return {
            "message": "File moved successfully",
            "file_id": file_id,
            "workspace_id": target_workspace_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving file {file_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while moving the file"
        )

