import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile, Request, Response, status, Query, Path
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session, joinedload
from redis.exceptions import RedisError
from ..utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional, TypeVar
from ..repositories.repository_manager import RepositoryManager
from fastapi.responses import JSONResponse
from ..dependencies import get_store, authenticate_user
from pydantic import BaseModel, Field
from ..schemas.learning_content import (
    StandardResponse,
    KeyConceptCreate, KeyConceptResponse, KeyConceptsListResponse, KeyConceptUpdate,
    FlashcardCreate, FlashcardResponse, FlashcardsListResponse, FlashcardUpdateRequest,
    QuizQuestionCreate, QuizQuestionResponse, QuizQuestionsListResponse, QuizQuestionUpdate
)
from ..models import KeyConcept as KeyConceptORM, Flashcard as FlashcardORM, QuizQuestion as QuizQuestionORM, File
import asyncio

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

    except Exception as e:
        logger.exception("Error during user authentication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
async def save_file(
    request: Request,
    language: str = Query(default="English"),
    comprehension_level: str = Query(default="Beginner"),
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

            file_id = await store.file_repo.add_file(user_id=user_id, file_name=url, file_url=url)
            if not file_id:
                raise HTTPException(status_code=500, detail="Failed to create file record for YouTube URL.")

            file_record = await store.file_repo.get_file_by_id(file_id=file_id)

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
                gcs_url = await upload_to_gcs(file, user_gc_id, file.filename)
                file_id = await store.file_repo.add_file(user_id=user_id, file_name=file.filename, file_url=gcs_url)
                if not file_id:
                    logger.error(f"Failed to create file record for {file.filename}")
                    continue

                file_record = await store.file_repo.get_file_by_id(file_id=file_id)
                uploaded_files_responses.append(FileResponse.model_validate(file_record))

            return UploadResponse(
                message="File(s) uploaded successfully.",
                files=uploaded_files_responses
            )

        else:
            raise HTTPException(status_code=400, detail="Unsupported content type.")

    except RedisError as e:
        logger.error(f"Redis error in save_file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="A caching error occurred.")
    except Exception as e:
        logger.error(f"Error saving file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Route to retrieve files
@files_router.get("", response_class=JSONResponse)
async def retrieve_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data['user_id']
        offset = (page - 1) * page_size
        paginated_result = await store.file_repo.get_files_for_user(user_id, skip=offset, limit=page_size)
        
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


# --- Learning Content Endpoints ---

# --- Flashcards ---

@files_router.get("/{file_id}/flashcards", response_model=StandardResponse[Dict[str, List[FlashcardResponse]]])
async def get_flashcards_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse:
    """
    Get all flashcards for a specific file.
    
    - **file_id**: The ID of the file to get flashcards for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting flashcards for file {file_id} (page {page}, size {page_size})")

        # File ownership already validated since file comes from user's file list
        # await check_ownership(file_id, user_data["user_id"], store)

        # Get the flashcards
        flashcards = await store.learning_material_repo.get_flashcards_for_file(file_id, page, page_size)
        
        logger.info(f"[API] Found {len(flashcards)} flashcards for file {file_id}")
        
        # Convert ORM objects to Pydantic models first
        flashcard_responses = [
            FlashcardResponse(
                id=fc.id,
                file_id=fc.file_id,
                question=fc.question,
                answer=fc.answer,
                is_custom=fc.is_custom,
                created_at=fc.created_at,
                difficulty=getattr(fc, 'difficulty', 'medium')
            ) for fc in flashcards
        ]
        
        return StandardResponse[Dict[str, List[FlashcardResponse]]](
            data={"flashcards": [fc.dict() for fc in flashcard_responses]},
            message="Flashcards retrieved successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting flashcards for file {file_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve flashcards"
        )

@files_router.post("/{file_id}/flashcards", response_model=StandardResponse[FlashcardResponse], status_code=status.HTTP_201_CREATED)
async def add_flashcard_for_file(
    file_id: int, 
    flashcard_data: FlashcardCreate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse[FlashcardResponse]:
    """
    Add a new flashcard to a file.
    
    - **file_id**: The ID of the file to add the flashcard to
    - **flashcard_data**: The flashcard data to add
    """
    try:
        logger.info(f"[API] Adding flashcard to file {file_id} by user {user_data['user_id']}")
        
        # Create a dictionary with the flashcard data to pass to the repository
        flashcard_dict = flashcard_data.dict()

        # Add the flashcard and get the new flashcard ID
        flashcard_id = await store.learning_material_repo.add_flashcard(
            file_id=file_id,
            flashcard_data=flashcard_dict
        )
        
        if not flashcard_id:
            logger.error(f"[API] Failed to create flashcard for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create flashcard in the database"
            )
        
        logger.info(f"[API] Successfully created flashcard {flashcard_id} for file {file_id}")
        
        # Convert to response model using the data we already have
        response_data = FlashcardResponse(
            id=flashcard_id,
            file_id=file_id,
            question=flashcard_dict['question'],
            answer=flashcard_dict['answer'],
            key_concept_id=flashcard_dict.get('key_concept_id'),
            is_custom=flashcard_dict.get('is_custom', False),
            created_at=datetime.utcnow(),
            difficulty=flashcard_dict.get('difficulty', 'medium')
        )
        
        return StandardResponse[FlashcardResponse](
            data=response_data.dict(),
            message="Flashcard created successfully",
            status_code=status.HTTP_201_CREATED
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding flashcard: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while adding the flashcard"
        )

@files_router.put("/{file_id}/flashcards/{flashcard_id}", response_model=StandardResponse[FlashcardResponse])
async def update_flashcard(
    file_id: int,
    flashcard_id: int, 
    flashcard_update_data: FlashcardUpdateRequest, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse[FlashcardResponse]:
    """
    Update an existing flashcard.
    
    - **file_id**: The ID of the file that the flashcard belongs to
    - **flashcard_id**: The ID of the flashcard to update
    - **flashcard_data**: The updated flashcard data
    """
    try:
        logger.info(f"[API] Updating flashcard {flashcard_id} for file {file_id} by user {user_data['user_id']}")

        # File ownership already validated since file comes from user's file list
        # await check_ownership(file_id, user_data["user_id"], store)

        # Update the flashcard - the repository will verify it belongs to the user
        updated_flashcard = await store.learning_material_repo.update_flashcard(
            flashcard_id=flashcard_id,
            user_id=user_data["user_id"],
            update_data=flashcard_update_data.dict(exclude_none=True)
        )
        if not updated_flashcard:
            logger.error(f"[API] Failed to update flashcard {flashcard_id} for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Flashcard not found or does not belong to the specified file"
            )
        
        logger.info(f"[API] Successfully updated flashcard {flashcard_id} for file {file_id}")
        
        # Convert to response model
        response_data = FlashcardResponse(
            id=updated_flashcard['id'],
            file_id=updated_flashcard['file_id'],
            question=updated_flashcard['question'],
            answer=updated_flashcard['answer'],
            key_concept_id=updated_flashcard['key_concept_id'],
            is_custom=updated_flashcard['is_custom'],
            created_at=updated_flashcard['created_at'],
            updated_at=updated_flashcard['updated_at'],
            difficulty=updated_flashcard.get('difficulty', 'medium')
        )
        
        return StandardResponse[FlashcardResponse](
            data=response_data.dict(),
            message="Flashcard updated successfully"
        )
        
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(f"Error updating flashcard: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the flashcard"
        )

@files_router.delete("/{file_id}/flashcards/{flashcard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flashcard(
    file_id: int,
    flashcard_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Delete a flashcard.
    
    - **file_id**: The ID of the file that the flashcard belongs to
    - **flashcard_id**: The ID of the flashcard to delete
    """
    try:
        logger.info(f"[API] Deleting flashcard {flashcard_id} for file {file_id} by user {user_data['user_id']}")

        # File ownership already validated since file comes from user's file list
        # await check_ownership(file_id, user_data["user_id"], store)

        # Delete the flashcard - the repository will verify it belongs to the user
        success = await store.learning_material_repo.delete_flashcard(
            flashcard_id=flashcard_id,
            user_id=user_data["user_id"]
        )
        
        if not success:
            logger.error(f"[API] Failed to delete flashcard {flashcard_id} for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Flashcard not found or does not belong to the specified file"
            )
        
        return Response(status_code=status.HTTP_204_NO_CONTENT)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting flashcard: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the flashcard"
        )

# --- Quiz Questions ---

@files_router.get("/{file_id}/quiz-questions", response_model=StandardResponse)
async def get_quiz_questions_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse:
    """
    Get all quiz questions for a specific file.
    
    - **file_id**: The ID of the file to get quiz questions for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting quiz questions for file {file_id} (page {page}, size {page_size})")

        # Get the quiz questions
        quiz_questions = await store.learning_material_repo.get_quiz_questions_for_file(file_id, page, page_size)
        
        logger.info(f"[API] Found {len(quiz_questions)} quiz questions for file {file_id}")
        
        # Convert ORM objects to Pydantic models
        question_responses = [
            QuizQuestionResponse(
                id=q.id,
                file_id=q.file_id,
                question=q.question,
                distractors=q.distractors or [],
                correct_answer=q.correct_answer,
                explanation=getattr(q, 'explanation', ''),
                key_concept_id=q.key_concept_id,
                is_custom=q.is_custom,
                created_at=q.created_at,
                difficulty=getattr(q, 'difficulty', 'medium'),
                question_type=getattr(q, 'question_type', 'MCQ')
            ) for q in quiz_questions
        ]
        
        return StandardResponse(
            data={"quizzes": [q.dict() for q in question_responses]},
            message="Quiz questions retrieved successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quiz questions for file {file_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve quiz questions"
        )

@files_router.post("/{file_id}/quiz-questions", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def add_quiz_question_for_file(
    file_id: int, 
    question_data: QuizQuestionCreate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse:
    """
    Add a new quiz question to a file.
    
    - **file_id**: The ID of the file to add the quiz question to
    - **question_data**: The quiz question data to add
    """
    try:
        logger.info(f"[API] Adding quiz question to file {file_id} by user {user_data['user_id']}")

        # File ownership already validated since file comes from user's file list
        # await check_ownership(file_id, user_data["user_id"], store)

        # Create a dictionary with the question data to pass to the repository
        question_dict = question_data.dict(exclude_none=True)

        # Add the quiz question and get the new question ID
        question_id = await store.learning_material_repo.add_quiz_question(
            file_id=file_id,
            **question_dict
        )
        
        if not question_id:
            logger.error(f"[API] Failed to create quiz question for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create quiz question in the database"
            )
        
        logger.info(f"[API] Successfully created quiz question {question_id} for file {file_id}")
        
        # Convert to response model using the data we already have
        response_data = QuizQuestionResponse(
            id=question_id,
            file_id=file_id,
            question=question_dict['question'],
            distractors=question_dict.get('distractors', []) or [],
            correct_answer=question_dict['correct_answer'],
            question_type=question_dict.get('question_type', 'MCQ'),
            key_concept_id=question_dict.get('key_concept_id'),
            is_custom=question_dict.get('is_custom', True),
            created_at=datetime.utcnow()
        )
        
        return StandardResponse(
            data=response_data.dict(),
            message="Quiz question created successfully",
            status_code=status.HTTP_201_CREATED
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding quiz question: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while adding the quiz question"
        )

@files_router.put("/{file_id}/quiz-questions/{quiz_question_id}", response_model=StandardResponse)
async def update_quiz_question(
    file_id: int,
    quiz_question_id: int, 
    quiz_question_data: QuizQuestionUpdate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse:
    """
    Update an existing quiz question.
    
    - **file_id**: The ID of the file that the quiz question belongs to
    - **quiz_question_id**: The ID of the quiz question to update
    - **quiz_question_data**: The updated quiz question data
    """
    try:
        logger.info(f"[API] Updating quiz question {quiz_question_id} for file {file_id} by user {user_data['user_id']}")

        # Update the quiz question - the repository will verify it belongs to the user
        updated_question = await store.learning_material_repo.update_quiz_question(
            quiz_question_id=quiz_question_id,
            user_id=user_data["user_id"],
            update_data=quiz_question_data.dict(exclude_none=True)
        )
        
        if not updated_question:
            logger.error(f"[API] Failed to update quiz question {quiz_question_id} for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Quiz question not found or does not belong to the specified file"
            )
        
        logger.info(f"[API] Successfully updated quiz question {quiz_question_id} for file {file_id}")
        
        # Convert to response model
        response_data = QuizQuestionResponse(
            id=updated_question['id'],
            file_id=updated_question['file_id'],
            question=updated_question['question'],
            distractors=updated_question.get('distractors', []) or [],
            correct_answer=updated_question['correct_answer'],
            question_type=updated_question.get('question_type', 'MCQ'),
            key_concept_id=updated_question.get('key_concept_id'),
            is_custom=updated_question.get('is_custom', True),
            created_at=updated_question['created_at'],
            updated_at=updated_question['updated_at']
        )
        
        return StandardResponse(
            data=response_data.dict(),
            message="Quiz question updated successfully"
        )
        
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(f"Error updating quiz question: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the quiz question"
        )

@files_router.delete("/{file_id}/quiz-questions/{quiz_question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quiz_question(
    file_id: int,
    quiz_question_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Delete a quiz question.
    
    - **file_id**: The ID of the file that the quiz question belongs to
    - **quiz_question_id**: The ID of the quiz question to delete
    """
    try:
        logger.info(f"[API] Deleting quiz question {quiz_question_id} for file {file_id} by user {user_data['user_id']}")

        # File ownership already validated since file comes from user's file list
        # await check_ownership(file_id, user_data["user_id"], store)

        # Delete the quiz question - the repository will verify it belongs to the user
        success = await store.learning_material_repo.delete_quiz_question(
            quiz_question_id=quiz_question_id,
            user_id=user_data["user_id"]
        )
        
        if not success:
            logger.error(f"[API] Failed to delete quiz question {quiz_question_id} for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Quiz question not found or does not belong to the specified file"
            )
        
        return Response(status_code=status.HTTP_204_NO_CONTENT)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting quiz question: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the quiz question"
        )

# --- Key Concepts ---

@files_router.get("/{file_id}/key-concepts", response_model=StandardResponse)
async def get_key_concepts_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse:
    # File ownership already validated since file comes from user's file list
    # await check_ownership(file_id, user_data["user_id"], store)
    concepts = await store.learning_material_repo.get_key_concepts_for_file(file_id, page, page_size)
    return StandardResponse(
        data=KeyConceptsListResponse(key_concepts=concepts).model_dump(),
        message="Key concepts retrieved successfully"
    )
@files_router.post("/{file_id}/key-concepts", response_model=StandardResponse[KeyConceptResponse], status_code=status.HTTP_201_CREATED)
async def add_key_concept(
    key_concept_data: KeyConceptCreate,
    file_id: int = Path(..., gt=0, description="The ID of the file to add the key concept to"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        logger.info(f"[API] Adding key concept for file {file_id} by user {user_data['user_id']}")

        # Add the key concept through the repository
        new_concept = await store.learning_material_repo.add_key_concept(
            file_id=file_id,
            key_concept_data=key_concept_data.dict()
        )
        
        if not new_concept:
            logger.error(f"[API] Failed to create key concept for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="Failed to create key concept in the database."
            )
        
        # Convert to response model
        response_data = KeyConceptResponse(
            id=new_concept['id'],
            file_id=new_concept['file_id'],
            concept_title=new_concept['concept_title'],
            concept_explanation=new_concept['concept_explanation'],
            source_page_number=new_concept['source_page_number'],
            source_video_timestamp_start_seconds=new_concept['source_video_timestamp_start_seconds'],
            source_video_timestamp_end_seconds=new_concept['source_video_timestamp_end_seconds'],
            is_custom=new_concept['is_custom'],
            created_at=new_concept['created_at'],
            updated_at=new_concept['updated_at']
        )
        
        logger.info(f"[API] Successfully created key concept {response_data.id} for file {file_id}")
        return StandardResponse(
            data=response_data.model_dump(),
            message="Key concept added successfully.",
            status_code=status.HTTP_201_CREATED
        )
        
    except ValueError as ve:
        logger.warning(f"[API] Validation error adding key concept: {ve}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error adding key concept: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while adding the key concept."
        )

@files_router.patch("/{file_id}/key-concepts/{key_concept_id}", response_model=StandardResponse[KeyConceptResponse], status_code=status.HTTP_200_OK)
async def update_key_concept(
    file_id: int,
    key_concept_id: int, 
    key_concept_update_data: KeyConceptUpdate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
):
    """
    Update an existing key concept.
    
    - **file_id**: The ID of the file that the key concept belongs to
    - **key_concept_id**: The ID of the key concept to update
    - **key_concept_data**: The updated key concept data
    """
    try:
        # File ownership already validated since file comes from user's file list
        # if not await store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
        #     raise HTTPException(
        #         status_code=status.HTTP_404_NOT_FOUND,
        #         detail="File not found or you don't have permission to access it."
        #     )

        # Update the key concept
        updated_concept = await store.learning_material_repo.update_key_concept(
            concept_id=key_concept_id,
            update_data=key_concept_update_data.dict(exclude_none=True)
        )
        
        if not updated_concept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key concept not found or doesn't belong to the specified file."
            )
        
        # Convert to response model
        response_data = KeyConceptResponse(
            id=updated_concept['id'],
            file_id=updated_concept['file_id'],
            concept_title=updated_concept['concept_title'],
            concept_explanation=updated_concept['concept_explanation'],
            source_page_number=updated_concept['source_page_number'],
            source_video_timestamp_start_seconds=updated_concept['source_video_timestamp_start_seconds'],
            source_video_timestamp_end_seconds=updated_concept['source_video_timestamp_end_seconds'],
            is_custom=updated_concept['is_custom'],
            created_at=updated_concept['created_at'],
            updated_at=updated_concept['updated_at']
        )
        
        return StandardResponse(
            data=response_data.model_dump(),
            message="Key concept updated successfully.",
            status_code=status.HTTP_200_OK
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating key concept {key_concept_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the key concept."
        )


@files_router.delete("/{file_id}/key-concepts/{key_concept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key_concept(
    file_id: int,
    key_concept_id: int, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
):
    # File ownership already validated since file comes from user's file list
    # await check_ownership(file_id, user_data["user_id"], store)
    success = await store.learning_material_repo.delete_key_concept(
        key_concept_id=key_concept_id,
        user_id=user_data["user_id"]
    )
    if not success:
        raise HTTPException(status_code=404, detail="Key concept not found or user does not have permission.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)