import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks, Query
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional, TypeVar, Generic, Any
from repositories.repository_manager import RepositoryManager
import json
from pydantic import BaseModel, Field, create_model, validator
from schemas.learning_content import KeyConceptUpdate, FlashcardUpdate, QuizQuestionUpdate
from llm_service import prompt_llm
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Define a standardized API response model
T = TypeVar('T')
class StandardResponse(BaseModel, Generic[T]):
    """Standardized API response format for all endpoints"""
    status: str = "success"
    data: T
    count: Optional[int] = None
    message: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "status": "success",
                "data": {},
                "count": 0,
                "message": "Data retrieved successfully"
            }
        }

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
    request: Request,
    language: str = Query(default="English", description="Language of the file content"),
    comprehension_level: str = Query(default="Beginner", description="Comprehension level of the file content"),
    files: Optional[List[UploadFile]] = File(None, description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user)
):
    try:
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
            file_id = store.add_file(user_id, url, url)  # Just store as a file with url as name and publicUrl
            if not file_id:
                logger.error(f"Failed to add YouTube URL {url} to database")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save YouTube information")
                
            # Let worker.py handle processing
            logger.info(f"Added YouTube link to database with ID {file_id}, worker.py will process it")
            return {"message": "YouTube video added. Processing will begin shortly."}

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

            # add_file returns just the file ID, not a dictionary
            file_id = store.add_file(user_id, file.filename, file_url)
            if not file_id:
                logger.error(f"Failed to add file {file.filename} to database")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save file information")
                
            # Let worker.py handle processing
            logger.info(f"Added file {file.filename} to database with ID {file_id}, worker.py will process it")

        return {"message": "File processing queued."}

    except RedisError as e:
        logger.error("Redis error")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to enqueue job")
    except Exception as e:
        logger.error(f"Exception occurred: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to retrieve files
@files_router.get("", response_model=Dict[str, Any])
async def retrieve_files(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Retrieve paginated list of files for the authenticated user.
    
    Returns:
        Dict: {
            'items': List[Dict],  # List of file records with metadata
            'total': int,         # Total number of files for the user
            'page': int,          # Current page number (1-based)
            'page_size': int      # Number of items per page
        }
    """
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        
        # Calculate skip value for pagination
        skip = (page - 1) * page_size
        
        # Get paginated files
        result = await store.get_files_for_user_async(
            user_id=user_id,
            skip=skip,
            limit=page_size
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error retrieving files: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="An error occurred while retrieving files"
        )

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
        
        # First, get the file name before deleting it
        file = store.get_file_by_id(file_id)
        if not file or file['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Now delete the file entry
        success = store.delete_file_entry(user_id, file_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File deletion failed")

        # Delete from GCS using the file name we got earlier
        delete_from_gcs(user_gc_id, file['file_name'])
        return None
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Define a Flashcard response model for better type safety
class FlashcardResponse(BaseModel):
    id: int
    file_id: int
    question: str
    answer: str
    key_concept_id: Optional[int] = None
    is_custom: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FlashcardsListResponse(BaseModel):
    flashcards: List[FlashcardResponse]

# Endpoint: Get all flashcards for a file
@files_router.get(
    "/{file_id}/flashcards", 
    response_model=StandardResponse[FlashcardsListResponse],
    summary="Get Flashcards for File",
    description="Retrieves all flashcards generated for a specific file."
)
async def get_flashcards_for_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        file = store.get_file_by_id(file_id)
        if not file or file['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Simply retrieve existing flashcards - no generation on request
        flashcards = store.get_flashcards_for_file(file_id)
        logger.info(f"Retrieved {len(flashcards) if flashcards else 0} flashcards for file {file_id}")
        
        # Format the flashcards for API response
        result = [FlashcardResponse.from_orm(card) for card in flashcards] if flashcards else []
        
        # Return standardized response
        response_data = FlashcardsListResponse(flashcards=result)
        return StandardResponse(
            status="success",
            data=response_data,
            count=len(result),
            message="Flashcards retrieved successfully"
        )
    except Exception as e:
        logger.error(f"Error fetching flashcards for file {file_id}: {e}")
        # Return error response with empty data instead of raising exception
        return StandardResponse(
            status="error",
            data=FlashcardsListResponse(flashcards=[]),
            count=0,
            message=f"Error retrieving flashcards: {str(e)[:100]}"
        )

# Endpoint: Add a flashcard for a file
class FlashcardCreate(BaseModel):
    question: str
    answer: str
    key_concept_id: Optional[int] = None
    is_custom: bool = True

@files_router.post(
    "/{file_id}/flashcards",
    response_model=StandardResponse[FlashcardResponse],
    summary="Add Flashcard for File",
    description="Creates a new flashcard for the specified file."
)
async def add_flashcard_for_file(
    file_id: int,
    flashcard: FlashcardCreate,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = get_store(request)
        
        # Verify file exists and belongs to user
        file_record = store.file_repo.get_file_by_id(file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Add the flashcard
        created_flashcard_orm = store.learning_material_repo.add_flashcard(
            file_id=file_id,
            question=flashcard.question,
            answer=flashcard.answer,
            key_concept_id=flashcard.key_concept_id,
            is_custom=flashcard.is_custom
        )

        if not created_flashcard_orm:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create flashcard.")

        response_data = FlashcardResponse.from_orm(created_flashcard_orm)
        
        return StandardResponse(
            status="success",
            data=response_data,
            message="Flashcard created successfully"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error adding flashcard for file {file_id}: {e}")
        return StandardResponse(
            status="error",
            data=FlashcardResponse(
                id=0,
                file_id=file_id,
                question=flashcard.question,
                answer=flashcard.answer,
                key_concept_id=flashcard.key_concept_id,
                is_custom=flashcard.is_custom
            ),
            count=0,
            message=f"Error creating flashcard: {str(e)[:100]}"
        )

@files_router.put("/flashcards/{flashcard_id}", response_model=StandardResponse[FlashcardResponse])
async def update_flashcard(
    flashcard_id: int,
    flashcard_update: FlashcardUpdate,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = get_store(request)

        # Verify user owns the file associated with the flashcard
        flashcard = store.get_flashcard_by_id(flashcard_id)
        if not flashcard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found.")

        file_record = store.file_repo.get_file_by_id(flashcard.file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found or access denied.")

        updated_flashcard = store.update_flashcard(
            flashcard_id,
            flashcard_update.dict(exclude_unset=True)
        )

        return StandardResponse(
            status="success",
            data=FlashcardResponse.from_orm(updated_flashcard),
            message="Flashcard updated successfully."
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating flashcard {flashcard_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not update flashcard.")

@files_router.delete("/flashcards/{flashcard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flashcard(
    flashcard_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = get_store(request)

        # Verify user owns the file associated with the flashcard
        flashcard = store.get_flashcard_by_id(flashcard_id)
        if not flashcard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found.")

        file_record = store.file_repo.get_file_by_id(flashcard.file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flashcard not found or access denied.")

        store.delete_flashcard(flashcard_id)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting flashcard {flashcard_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not delete flashcard.")

# --- Quiz Learning Content ---

class QuizQuestionResponse(BaseModel):
    id: int
    file_id: int
    key_concept_id: Optional[int] = None
    question: str
    question_type: str = "MCQ"
    correct_answer: str = ""
    distractors: List[str] = []
    explanation: Optional[str] = ""
    difficulty: Optional[str] = "medium"
    is_custom: bool = False

    class Config:
        from_attributes = True

    @validator('distractors', pre=True, allow_reuse=True)
    def parse_json_string(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse distractors string to JSON: {v}")
                return []
        return v

class QuizzesListResponse(BaseModel):
    quizzes: List[QuizQuestionResponse]

class QuizQuestionCreate(BaseModel):
    question: str
    correct_answer: str
    distractors: List[str] = []
    key_concept_id: Optional[int] = None
    question_type: str = "MCQ"
    explanation: Optional[str] = ""
    difficulty: str = "medium"
    is_custom: bool = True

@files_router.get(
    "/{file_id}/quizzes",
    response_model=StandardResponse[QuizzesListResponse],
    summary="Get Quiz Questions for File",
    description="Retrieves all quiz questions generated for a specific file."
)
async def get_quizzes_for_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        file = store.get_file_by_id(file_id)
        if not file or file['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        quizzes = store.get_quiz_questions_for_file(file_id)
        logger.info(f"Retrieved {len(quizzes) if quizzes else 0} quizzes for file {file_id}")
        
        quizzes_out = [QuizQuestionResponse.from_orm(q) for q in quizzes]
        
        response_data = QuizzesListResponse(quizzes=quizzes_out)
        return StandardResponse(
            status="success",
            data=response_data,
            count=len(quizzes_out),
            message="Quizzes retrieved successfully"
        )
    except Exception as e:
        logger.error(f"Error getting quizzes for file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not retrieve quizzes")

@files_router.post(
    "/{file_id}/quizzes",
    response_model=StandardResponse[QuizQuestionResponse],
    summary="Add Quiz Question for File",
    description="Creates a new quiz question for the specified file."
)
async def add_quiz_question_for_file(
    file_id: int,
    quiz_question: QuizQuestionCreate,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    """Manually add a single quiz question to a file."""
    try:
        user_id = user_data["user_id"]
        store = get_store(request)

        file_record = store.file_repo.get_file_by_id(file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or access denied.")

        # Prepare data, converting key_concept_id 0 to None
        quiz_data = quiz_question.dict()
        key_concept_id = quiz_data.pop("key_concept_id", None)
        if key_concept_id == 0:
            key_concept_id = None

        # Add quiz question to the database, ensuring only valid fields are passed
        new_quiz_id = store.learning_material_repo.add_quiz_question(
            file_id=file_id,
            question=quiz_question.question,
            question_type=quiz_question.question_type,
            correct_answer=quiz_question.correct_answer,
            distractors=quiz_question.distractors,
            key_concept_id=key_concept_id,
            is_custom=quiz_question.is_custom
        )

        if not new_quiz_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create the quiz question.")

        # Fetch the full object to ensure it's attached to a session
        new_quiz_orm = store.learning_material_repo.get_quiz_question_by_id(new_quiz_id)
        if not new_quiz_orm:
            raise HTTPException(status_code=404, detail="Quiz question not found after creation")

        return StandardResponse(
            status="success",
            data=QuizQuestionResponse.from_orm(new_quiz_orm),
            message="Quiz question added successfully."
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error adding quiz question for file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not add quiz question.")

@files_router.post("/{file_id}/key_concepts", response_model=StandardResponse[KeyConceptResponse], status_code=status.HTTP_201_CREATED)
async def add_key_concept_for_file(
    file_id: int,
    key_concept_data: KeyConceptCreate,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    """Manually add a single key concept to a file."""
    try:
        user_id = user_data["user_id"]
        store = get_store(request)

        # Verify user owns the file
        file_record = store.file_repo.get_file_by_id(file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or access denied.")

        # Call the repository method to add a single key concept
        new_concept_id = store.learning_material_repo.add_key_concept(
            file_id=file_id,
            **key_concept_data.dict()
        )

        if not new_concept_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not create the key concept.")

        # Fetch the full object to avoid DetachedInstanceError
        new_concept_orm = store.learning_material_repo.get_key_concept_by_id(new_concept_id)
        if not new_concept_orm:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key concept not found after creation")

        # Convert the ORM object to a Pydantic response model
        response_data = KeyConceptResponse.from_orm(new_concept_orm)

        return StandardResponse(
            status="success",
            data=response_data,
            message="Key concept added successfully."
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error adding key concept for file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not add key concept.")

@files_router.delete("/key_concepts/{key_concept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key_concept(
    key_concept_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = get_store(request)

        # Verify user owns the file associated with the key concept
        key_concept = store.get_key_concept_by_id(key_concept_id)
        if not key_concept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key concept not found.")

        file_record = store.file_repo.get_file_by_id(key_concept.file_id)
        if not file_record or file_record['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key concept not found or access denied.")

        store.delete_key_concept(key_concept_id)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error deleting key concept {key_concept_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not delete key concept.")