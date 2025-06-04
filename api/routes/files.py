import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks, Query
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional
from repositories.repository_manager import RepositoryManager
from pydantic import BaseModel, Field
from llm_service import prompt_llm
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request, store: RepositoryManager = Depends(get_store)):
    try:
        token = request.headers.get('Authorization')
        if not token:
            logger.error("Missing Authorization token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        success, user_info = get_user_id(token)
        if not success:
            logger.error("Failed to authenticate user with token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        user_id = store.get_user_id_from_email(user_info['email'])
        if not user_id:
            logger.error(f"No user ID found for email: {user_info['email']}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info(f"Authenticated user_id: {user_id}, user_gc_id: {user_info['user_id']}")
        return {"user_id": user_id, "user_gc_id": user_info['user_id']}

    except Exception as e:
        logger.exception("Error during user authentication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def save_file(
    background_tasks: BackgroundTasks,
    request: Request,
    language: str = Query(default="English", description="Language of the file content"),
    comprehension_level: str = Query(default="Beginner", description="Comprehension level of the file content"),
    files: Optional[List[UploadFile]] = File(None, description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user)
):
    try:
        from tasks import process_file_data  # Ensure this import is correct
        import re
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        store = request.app.state.store

        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            # --- Handle YouTube JSON payload ---
            data = await request.json()
            if not (data and isinstance(data, dict) and data.get("type") == "youtube"):
                raise HTTPException(status_code=400, detail="Invalid payload for YouTube link upload.")
            url = data.get("url", "")

            # Validate YouTube URL
            youtube_regex = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/")
            if not url or not youtube_regex.match(url):
                raise HTTPException(status_code=400, detail="Invalid YouTube URL.")
            # Store YouTube file entry (type: 'youtube')
            file_info = store.add_file(user_id, url, url)  # Just store as a file with url as name and publicUrl
            background_tasks.add_task(process_file_data, user_gc_id, user_id, file_info['id'], url, url, True, language=language, comprehension_level=comprehension_level)  # True = is_youtube
            logger.info(f"Enqueued Task for processing YouTube link: {url}")
            return {"message": "YouTube video processing queued."}

        # --- Handle regular file upload as before ---
        if not files:
            logger.warning('No files provided')
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided")

        for file in files:
            if not file.filename:
                logger.warning('File has no filename')
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File has no filename")

            file_url = await upload_to_gcs(file, user_gc_id, file.filename)
            if not file_url:
                logger.error(f"Failed to upload {file.filename} to GCS")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File upload failed")

            # Capture the returned file info, including the ID
            file_info = store.add_file(user_id, file.filename, file_url)
            background_tasks.add_task(process_file_data, user_gc_id, user_id, file_info['id'], file.filename, file_info['file_url'], language=language, comprehension_level=comprehension_level)
            logger.info(f"Enqueued Task for processing {file.filename}")

        return {"message": "File processing queued."}

    except RedisError as e:
        logger.error("Redis error")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to enqueue job")
    except Exception as e:
        logger.error(f"Exception occurred: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to retrieve files
@files_router.get("", response_model=List[Dict])
async def retrieve_files(
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        files = store.get_files_for_user(user_id)
        return files
    except Exception as e:
        logger.error(f"Error retrieving files: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to delete a file
@files_router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        store = request.app.state.store
        file_info = store.delete_file_entry(user_id, file_id)
        if not file_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        delete_from_gcs(user_gc_id, file_info['file_name'])
        return None
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Endpoint: Get all flashcards for a file
@files_router.get("/{file_id}/flashcards")
async def get_flashcards_for_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        file = store.get_file_by_id(file_id)
        if not file or file.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
        flashcards = store.get_flashcards_for_file(file_id)
        # Convert SQLAlchemy objects to dicts for frontend
        flashcards_out = []
        
        for f in flashcards:
            # Make sure none of the fields are None/null to avoid UI issues
            flashcard_data = {
                "id": f.id,
                "file_id": f.file_id,
                "key_concept_id": f.key_concept_id,
                "question": f.question if f.question else "",
                "answer": f.answer if f.answer else "",
                "is_custom": f.is_custom if f.is_custom is not None else False
            }
            flashcards_out.append(flashcard_data)
        # Wrap the flashcards in an object with a named property as expected by the frontend
        return {"flashcards": flashcards_out}
    except Exception as e:
        logger.error(f"Error fetching flashcards for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint: Get all quiz questions for a file
@files_router.get("/{file_id}/quizzes")
async def get_quizzes_for_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        file = store.get_file_by_id(file_id)
        if not file or file.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
        quizzes = store.get_quiz_questions_for_file(file_id)
        quizzes_out = []
        for q in quizzes:
            # Ensure distractors is properly serialized JSON
            if q.distractors is None:
                distractors = []
            elif isinstance(q.distractors, list):
                distractors = q.distractors
            else:
                # Handle string or other unexpected formats
                try:
                    # If it's a string representation of JSON, parse it
                    if isinstance(q.distractors, str):
                        import json
                        distractors = json.loads(q.distractors)
                    else:
                        # Default to empty list if we can't parse
                        distractors = []
                except Exception as e:
                    logger.error(f"Error parsing distractors: {e}")
                    distractors = []
            
            quizzes_out.append({
                "id": q.id,
                "file_id": q.file_id,
                "key_concept_id": q.key_concept_id,
                "question": q.question if q.question else "",
                "question_type": q.question_type if q.question_type else "unknown",
                "correct_answer": q.correct_answer if q.correct_answer else "",
                "distractors": distractors
            })
        # Wrap the quizzes in an object with a named property as expected by the frontend
        return {"quizzes": quizzes_out}
    except Exception as e:
        logger.error(f"Error fetching quizzes for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to re-extract a file (retry processing)
@files_router.patch("/{file_id}/reextract", status_code=status.HTTP_202_ACCEPTED)
async def reextract_file(
    file_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        from tasks import process_file_data
        
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        store = request.app.state.store

        # Get file information
        file_info = store.get_file_by_id(file_id)
        if not file_info or file_info.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Reset processing status to mark it for reprocessing
        store.update_file_processing_status(file_id, False)
        
        # Check if it's a YouTube file
        is_youtube = 'youtube' in file_info.file_name.lower()
        
        # Restart processing task
        background_tasks.add_task(
            process_file_data, 
            user_gc_id, 
            user_id, 
            file_id, 
            file_info.file_name,
            file_info.file_url,
            is_youtube
        )
        
        logger.info(f"Restarted processing for file ID: {file_id}, name: {file_info.file_name}")
        return {"message": "File processing restarted successfully"}
        
    except Exception as e:
        logger.error(f"Error reextracting file: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Pydantic models for Key Concepts
class KeyConceptResponse(BaseModel):
    id: int
    file_id: int
    concept_title: Optional[str] = None
    concept_explanation: Optional[str] = None
    display_order: Optional[int] = None
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True

class KeyConceptsFileResponse(BaseModel):
    key_concepts: List[KeyConceptResponse]

# Route to get key concepts for a file
@files_router.get("/{file_id}/key_concepts", response_model=KeyConceptsFileResponse, summary="Get Key Concepts for File", description="Retrieves all key concepts extracted for a specific file.")
async def get_key_concepts_for_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        store = request.app.state.store
        user_id = user_data["user_id"]
        
        # Skip File model validation and use direct SQL query to check permissions
        try:
            # Simple query to check if file exists and belongs to user
            with store.get_engine().connect() as conn:
                result = conn.execute(f"SELECT 1 FROM files WHERE id = {file_id} AND user_id = {user_id} LIMIT 1")
                if not result.scalar():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
        except Exception as auth_error:
            # If we can't check authorization due to database issues, log and continue
            logger.warning(f"Could not verify file ownership: {auth_error}. Continuing anyway.")
        
        try:
            # Get key concepts directly with a robust query
            key_concepts = store.get_key_concepts_for_file(file_id)
            
            # Explicitly convert SQLAlchemy KeyConcept objects to KeyConceptResponse Pydantic models.
            # Handle any potential errors during conversion
            key_concept_responses = []
            for kc in key_concepts:
                try:
                    key_concept_responses.append(KeyConceptResponse.from_orm(kc))
                except Exception as orm_error:
                    logger.warning(f"Error converting key concept to response model: {orm_error}")
                    # Try manual conversion with defaults
                    try:
                        key_concept_responses.append(KeyConceptResponse(
                            id=kc.id,
                            file_id=kc.file_id,
                            concept=kc.concept if hasattr(kc, 'concept') else "",
                            explanation=kc.explanation if hasattr(kc, 'explanation') else "",
                            span_text=kc.span_text if hasattr(kc, 'span_text') else "",
                            span_start=kc.span_start if hasattr(kc, 'span_start') else 0,
                            span_end=kc.span_end if hasattr(kc, 'span_end') else 0
                        ))
                    except:
                        # Skip this key concept if we can't convert it
                        pass
            
            return KeyConceptsFileResponse(key_concepts=key_concept_responses)
        except Exception as kc_error:
            logger.error(f"Error retrieving key concepts: {kc_error}")
            # Return empty list instead of error
            return KeyConceptsFileResponse(key_concepts=[])
    except Exception as e:
        logger.error(f"Error in key concepts endpoint for file {file_id}: {e}", exc_info=True)
        # Return empty response instead of error
        return KeyConceptsFileResponse(key_concepts=[])