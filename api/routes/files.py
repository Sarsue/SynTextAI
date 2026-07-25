import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile, Request, Response, status, Query, Path
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session, joinedload
from redis.exceptions import RedisError
from ..core.utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
import asyncio
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

# PRODUCTION: File size limits to prevent OOM and abuse
MAX_FILE_SIZE_MB = 100  # 100MB max for PDFs
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
from typing import Dict, List, Optional, TypeVar
from ..repositories.repository_manager import RepositoryManager
from fastapi.responses import JSONResponse
from ..core.dependencies import get_store, authenticate_user
from ..core.limits import assert_can_create_doc
from pydantic import BaseModel, Field, validator
from ..models import File

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

LANGUAGE_CODE_MAP = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
}


async def _assert_youtube_transcript_available(video_id: str, target_lang_code: str) -> None:
    if not YouTubeTranscriptApi:
        raise HTTPException(status_code=503, detail="YouTube transcript support is not available on this server.")

    languages = [target_lang_code] if target_lang_code == "en" else [target_lang_code, "en"]
    try:
        transcript = await asyncio.to_thread(
            YouTubeTranscriptApi.get_transcript,
            video_id,
            languages=languages,
        )
    except Exception as e:
        logger.info(f"Rejected YouTube URL; transcript unavailable for video_id={video_id}: {e}")
        raise HTTPException(
            status_code=400,
            detail="This YouTube video doesn't have captions/transcript available. Please choose a video with captions (CC).",
        )

    if not transcript:
        raise HTTPException(
            status_code=400,
            detail="This YouTube video doesn't have captions/transcript available. Please choose a video with captions (CC).",
        )

# Define a standardized API response model
T = TypeVar('T')

# Dependency to get the store
def get_store(request: Request) -> RepositoryManager:
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request, store: RepositoryManager = Depends(get_store)) -> Dict[str, any]:
    try:
        token = request.headers.get('Authorization')
        if not token:
            logger.error("Missing Authorization token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        success, user_info = get_user_id(token)
        if not success or not user_info:
            logger.error("Failed to authenticate user with token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
        if not user_id:
            logger.error(f"No user ID found for email: {user_info['email']}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info(f"Authenticated user_id: {user_id}, user_gc_id: {user_info['user_id']}")
        return {"user_id": user_id, "user_gc_id": user_info['user_id']}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during user authentication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

async def check_ownership(file_id: int, user_id: int, store: RepositoryManager) -> None:
    """Raise 404 unless file_id exists and belongs to user_id.

    Learning-content endpoints (flashcards/quiz-questions/key-concepts) are keyed
    off file_id in the URL, so this is the gate that stops one user from reading
    or writing another user's derived document content by guessing/incrementing
    file_id.
    """
    file_record = await store.file_repo.get_file_by_id(file_id)
    if not file_record or file_record.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
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
        user_gc_id = user_data["user_gc_id"]

        content_type = request.headers.get("content-type", "")

        # --- YouTube JSON Upload ---
        if content_type.startswith("application/json"):
            data = await request.json()
            if not (data and isinstance(data, dict) and data.get("type") == "youtube"):
                raise HTTPException(status_code=400, detail="Invalid payload for YouTube link upload.")

            url = data.get("url", "")
            # Improved regex to match various YouTube URL formats and extract video ID
            youtube_regex = re.compile(r"(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|m\.youtube\.com)/(?:watch\?v=|embed/|v/|)([a-zA-Z0-9_-]{11})")
            match = youtube_regex.search(url)
            if not url or not match:
                raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

            video_id = match.group(1)

            target_lang_code = LANGUAGE_CODE_MAP.get((language or "English").lower(), "en")
            await _assert_youtube_transcript_available(video_id=video_id, target_lang_code=target_lang_code)

            # For YouTube, the stored artifacts are typically transcripts and derived data.
            # Treat the initial record as a very small logical size to avoid blocking later
            # transcription-based accounting; this keeps enforcement simple for now.
            await assert_can_create_doc(store, user_id, new_doc_size_bytes=0)
            
            # If workspace_id not provided, get user's first workspace (default)
            actual_workspace_id = workspace_id
            if not actual_workspace_id:
                workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
                if workspaces:
                    actual_workspace_id = workspaces[0]["id"]
                    logger.info(f"Using default workspace {actual_workspace_id} for user {user_id}")

            file_id = await store.file_repo.add_file(
                user_id=user_id, 
                file_name=url, 
                file_url=url, 
                file_size_bytes=0,
                workspace_id=actual_workspace_id
            )
            if not file_id:
                raise HTTPException(status_code=500, detail="Failed to create file record for YouTube URL.")

            file_record = await store.file_repo.get_file_by_id(file_id=file_id)

            await store.agent_run_repo.enqueue_run(
                run_type="ingest_file",
                agent_name="IngestionAgent",
                agent_version=None,
                payload={
                    "file_id": int(file_id),
                    "user_id": int(user_id),
                    "workspace_id": int(actual_workspace_id) if actual_workspace_id is not None else None,
                    "filename": url,
                    "file_url": url,
                    "is_youtube": True,
                    "language": language,
                    "comprehension_level": comprehension_level,
                },
                user_id=int(user_id),
                workspace_id=int(actual_workspace_id) if actual_workspace_id is not None else None,
                file_id=int(file_id),
                priority=200,
                max_attempts=3,
            )

            return UploadResponse(
                message="YouTube link processed successfully.",
                files=[FileResponse.model_validate(file_record)]
            )

        # --- Multipart File Upload ---
        elif content_type.startswith("multipart/form-data"):
            if not files:
                raise HTTPException(status_code=400, detail="No files were uploaded.")

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

                # Enforce per-user document count and storage limits for free plans
                await assert_can_create_doc(store, user_id, new_doc_size_bytes=file_size)

                # Reset file pointer after reading
                await file.seek(0)
                
                # If workspace_id not provided, get user's first workspace (default)
                actual_workspace_id = workspace_id
                if not actual_workspace_id:
                    workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
                    if workspaces:
                        actual_workspace_id = workspaces[0]["id"]
                        logger.info(f"Using default workspace {actual_workspace_id} for user {user_id}")
                
                gcs_url = await upload_to_gcs(file, user_gc_id, file.filename)
                if not gcs_url:
                    logger.error(f"Failed to upload {file.filename} to storage (gcs_url is empty)")
                    raise HTTPException(status_code=500, detail="Failed to upload file to storage")
                file_id = await store.file_repo.add_file(
                    user_id=user_id, 
                    file_name=file.filename, 
                    file_url=gcs_url, 
                    file_size_bytes=file_size,
                    workspace_id=actual_workspace_id
                )
                if not file_id:
                    logger.error(f"Failed to create file record for {file.filename}")
                    continue

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
                        "is_youtube": False,
                        "language": language,
                        "comprehension_level": comprehension_level,
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
        raise HTTPException(status_code=500, detail=str(e))

# Route to retrieve files
@files_router.get("", response_class=JSONResponse)
async def retrieve_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    workspace_id: Optional[int] = Query(None, description="Filter by workspace ID"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data['user_id']
        offset = (page - 1) * page_size
        paginated_result = await store.file_repo.get_files_for_user(user_id, skip=offset, limit=page_size, workspace_id=workspace_id)
        
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
            }
            for f in db_files
        ]

        return {
            "items": response_items,
            "page": page,
            "page_size": page_size,
            "total": total_files,
        }
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
        "generating_concepts": 90,
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
        if not file_record or file_record.get("user_id") != user_data["user_id"]:
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
        for sid in raw_ids:
            try:
                fid = int(sid)
            except ValueError:
                continue
            file_record = await store.file_repo.get_file_by_id(file_id=fid)
            if not file_record or file_record.get("user_id") != user_data["user_id"]:
                continue
            status_str = file_record.get("processing_status", "uploaded")
            results.append({
                "file_id": fid,
                "processing_status": status_str,
                "progress": _status_to_progress(status_str),
            })
        return {"items": results}
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
        user_gc_id = user_data["user_gc_id"]

        file_to_delete = await store.file_repo.get_file_by_id(file_id)
        if not file_to_delete or file_to_delete.get('user_id') != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized.")

        if file_to_delete.get('file_url') and "storage.googleapis.com" in file_to_delete.get('file_url'):
             await asyncio.to_thread(delete_from_gcs, user_gc_id, file_to_delete.get('file_name'))

        if not await store.file_repo.delete_file_entry(user_id, file_id):
             raise HTTPException(status_code=500, detail="Failed to delete file entry.")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete file.")


# Move file to different workspace
class MoveFileRequest(BaseModel):
    workspace_id: int = Field(..., description="Target workspace ID")

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
        
        if file['user_id'] != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to move this file"
            )
        
        # Verify target workspace exists and belongs to user
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
        workspace_ids = [ws['id'] for ws in workspaces]
        
        if target_workspace_id not in workspace_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Target workspace not found or doesn't belong to you"
            )
        
        # Update file's workspace
        success = await store.file_repo.update_file_workspace(file_id, target_workspace_id)
        
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

