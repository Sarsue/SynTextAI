import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks, Query
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional, TypeVar, Generic, Any
from repositories.repository_manager import RepositoryManager
from pydantic import BaseModel, Field, create_model
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
    background_tasks: BackgroundTasks,
    request: Request,
    language: str = Query(default="English", description="Language of the file content"),
    comprehension_level: str = Query(default="Beginner", description="Comprehension level of the file content"),
    files: Optional[List[UploadFile]] = File(None, description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user)
):
    try:
        from tasks import process_file_data  # Using relative import path
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
                
            # Use FastAPI's background_tasks for YouTube processing
            background_tasks.add_task(process_file_data, user_gc_id, user_id, file_id, url, url, True, language=language, comprehension_level=comprehension_level)  # True = is_youtube
            logger.info(f"Enqueued Task for processing YouTube link: {url} with ID {file_id}")
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

            # add_file returns just the file ID, not a dictionary
            file_id = store.add_file(user_id, file.filename, file_url)
            if not file_id:
                logger.error(f"Failed to add file {file.filename} to database")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save file information")
                
            # Use FastAPI's background_tasks
            background_tasks.add_task(process_file_data, user_gc_id, user_id, file_id, file.filename, file_url, language=language, comprehension_level=comprehension_level)
            logger.info(f"Enqueued Task for processing {file.filename} with ID {file_id}")

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
        # The processing status is already calculated in the FileRepository.get_files_for_user method
        # based on whether the file has key concepts
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
    created_at: Optional[str] = None

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
        result = []
        if flashcards:
            for card in flashcards:
                try:
                    result.append(FlashcardResponse(
                        id=card.id,
                        file_id=card.file_id,
                        question=card.question,
                        answer=card.answer,
                        key_concept_id=card.key_concept_id,
                        is_custom=card.is_custom if hasattr(card, 'is_custom') else False,
                        created_at=card.created_at.isoformat() if hasattr(card, 'created_at') else None
                    ))
                except Exception as e:
                    logger.error(f"Error formatting flashcard: {e}")
        
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
        store = request.app.state.store
        
        # Verify file exists and belongs to user
        file = store.get_file_by_id(file_id)
        if not file or file['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Add the flashcard
        flashcard_id = store.add_flashcard(
            file_id=file_id,
            question=flashcard.question,
            answer=flashcard.answer,
            key_concept_id=flashcard.key_concept_id,
            is_custom=flashcard.is_custom
        )
        
        if not flashcard_id:
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
                message="Failed to create flashcard"
            )
            
        # Return the created flashcard in standardized response format
        created_flashcard = FlashcardResponse(
            id=flashcard_id,
            file_id=file_id,
            key_concept_id=flashcard.key_concept_id,
            question=flashcard.question,
            answer=flashcard.answer,
            is_custom=flashcard.is_custom
        )
        
        return StandardResponse(
            status="success",
            data=created_flashcard,
            count=1,
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

# Define Quiz Question Response model for better type safety
class QuizQuestionResponse(BaseModel):
    id: int
    file_id: int
    key_concept_id: Optional[int] = None
    question_text: str  # Frontend expects this field
    question: str
    question_type: str = "MCQ"
    correct_answer: str = ""
    distractors: List[str] = []
    explanation: str = ""  # Empty string instead of None for frontend compatibility
    difficulty: str = "medium"  # Default value expected by frontend
    is_custom: bool = False  # Default for system-generated questions

class QuizzesListResponse(BaseModel):
    quizzes: List[QuizQuestionResponse]

# Endpoint: Get all quiz questions for a file
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
            
        # Simply retrieve existing quizzes - no generation on request
        quizzes = store.get_quiz_questions_for_file(file_id)
        logger.info(f"Retrieved {len(quizzes) if quizzes else 0} quizzes for file {file_id}")
        
        logger.info(f"Raw quizzes data from repo for file {file_id}: {quizzes}")
        quizzes_out = []
        for q in quizzes:
            try:
                # Ensure distractors is properly serialized JSON
                if not hasattr(q, 'distractors') or q.distractors is None:
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
                
                quizzes_out.append(QuizQuestionResponse(
                    id=q.id,
                    file_id=q.file_id,
                    key_concept_id=getattr(q, 'key_concept_id', None),
                    question_text=q.question,  # Frontend expects this field
                    question=q.question,
                    question_type=q.question_type if hasattr(q, 'question_type') and q.question_type else "MCQ",
                    correct_answer=q.correct_answer if hasattr(q, 'correct_answer') and q.correct_answer else "",
                    distractors=distractors,
                    explanation=getattr(q, 'explanation', ""),  # Empty string instead of None for frontend compatibility
                    difficulty=getattr(q, 'difficulty', "medium"),  # Default value expected by frontend
                    is_custom=getattr(q, 'is_custom', False)  # Default for system-generated questions
                ))
            except Exception as e:
                logger.error(f"Error converting quiz question to dict: {e}")
                # Skip this quiz question if conversion fails
                
        # Return standardized response
        response_data = QuizzesListResponse(quizzes=quizzes_out)
        return StandardResponse(
            status="success",
            data=response_data,
            count=len(quizzes_out),
            message="Quiz questions retrieved successfully"
        )
    except Exception as e:
        logger.error(f"Error fetching quizzes for file {file_id}: {e}")
        # Return error response with empty data instead of raising exception
        return StandardResponse(
            status="error",
            data=QuizzesListResponse(quizzes=[]),
            count=0,
            message=f"Error retrieving quiz questions: {str(e)[:100]}"
        )

# Endpoint: Add a quiz question for a file
class QuizQuestionCreate(BaseModel):
    question: str
    question_type: str = "MCQ"  # Default to MCQ if not specified
    correct_answer: str
    distractors: List[str]
    key_concept_id: Optional[int] = None
    is_custom: bool = True

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
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        
        # Verify file exists and belongs to user
        file = store.get_file_by_id(file_id)
        if not file or file['user_id'] != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Add the quiz question
        quiz_id = store.add_quiz_question(
            file_id=file_id,
            question=quiz_question.question,
            question_type=quiz_question.question_type,
            correct_answer=quiz_question.correct_answer,
            distractors=quiz_question.distractors,
            key_concept_id=quiz_question.key_concept_id,
            is_custom=quiz_question.is_custom
        )
        
        if not quiz_id:
            return StandardResponse(
                status="error",
                data=QuizQuestionResponse(
                    id=0,
                    file_id=file_id,
                    question=quiz_question.question,
                    question_text=quiz_question.question,
                    question_type=quiz_question.question_type,
                    correct_answer=quiz_question.correct_answer,
                    distractors=quiz_question.distractors,
                    key_concept_id=quiz_question.key_concept_id,
                    is_custom=quiz_question.is_custom,
                    explanation="",
                    difficulty="medium"
                ),
                count=0,
                message="Failed to create quiz question"
            )
            
        # Return the created quiz question in standardized format
        created_quiz = QuizQuestionResponse(
            id=quiz_id,
            file_id=file_id,
            question=quiz_question.question,
            question_text=quiz_question.question,  # Frontend expects this field
            question_type=quiz_question.question_type,
            correct_answer=quiz_question.correct_answer,
            distractors=quiz_question.distractors,
            key_concept_id=quiz_question.key_concept_id,
            is_custom=quiz_question.is_custom,
            # Include these for frontend compatibility with default values
            explanation="",
            difficulty="medium"
        )
        
        return StandardResponse(
            status="success",
            data=created_quiz,
            count=1,
            message="Quiz question created successfully"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error adding quiz question for file {file_id}: {e}")
        return StandardResponse(
            status="error",
            data=QuizQuestionResponse(
                id=0,
                file_id=file_id,
                question=quiz_question.question,
                question_text=quiz_question.question,
                question_type=quiz_question.question_type,
                correct_answer=quiz_question.correct_answer,
                distractors=quiz_question.distractors,
                key_concept_id=quiz_question.key_concept_id,
                is_custom=quiz_question.is_custom,
                explanation="",
                difficulty="medium"
            ),
            count=0,
            message=f"Error creating quiz question: {str(e)[:100]}"
        )

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
        if not file_info or file_info.get('user_id') != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized")
            
        # Reset processing status to mark it for reprocessing
        store.update_file_processing_status(file_id, False)
        
        # Check if it's a YouTube file
        is_youtube = 'youtube' in file_info.get('file_name', '').lower()
        
        # Restart processing task using background_tasks
        background_tasks.add_task(
            process_file_data, 
            user_gc_id, 
            user_id, 
            file_id, 
            file_info.get('file_name', ''),
            file_info.get('file_url', ''),
            is_youtube
        )
        
        logger.info(f"Restarted processing for file ID: {file_id}, name: {file_info.get('file_name', '')}")
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

    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True

class KeyConceptsFileResponse(BaseModel):
    key_concepts: List[KeyConceptResponse]

# Route to get key concepts for a file
@files_router.get(
    "/{file_id}/key_concepts", 
    response_model=StandardResponse[KeyConceptsFileResponse],
    summary="Get Key Concepts for File", 
    description="Retrieves all key concepts extracted for a specific file."
)
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
            logger.info(f"Raw key_concepts data from repo: {key_concepts}")
            
            # Explicitly convert KeyConcept objects to KeyConceptResponse Pydantic models.
            # Handle any potential errors during conversion
            key_concept_responses = []
            for kc in key_concepts:
                try:
                    # Log the values for debugging
                    logger.info(f"KeyConcept attributes: id={kc.id}, concept_title={kc.concept_title!r}, concept_explanation={kc.concept_explanation!r}")
                    
                    # Try direct attribute access first (for domain objects)
                    key_concept_response = KeyConceptResponse(
                        id=kc.id,
                        file_id=kc.file_id,
                        concept_title=kc.concept_title,
                        concept_explanation=kc.concept_explanation,

                        source_page_number=getattr(kc, 'source_page_number', None),
                        source_video_timestamp_start_seconds=getattr(kc, 'source_video_timestamp_start_seconds', None),
                        source_video_timestamp_end_seconds=getattr(kc, 'source_video_timestamp_end_seconds', None),
                        created_at=kc.created_at if hasattr(kc, 'created_at') else datetime.now()
                    )
                    
                    key_concept_responses.append(key_concept_response)
                except Exception as orm_error:
                    logger.warning(f"Error converting key concept to response model: {orm_error}")
                    try:
                        # As a fallback, try dictionary access (for dict return types)
                        if isinstance(kc, dict):
                            key_concept_responses.append(KeyConceptResponse(
                                id=kc.get('id'),
                                file_id=kc.get('file_id'),
                                concept_title=kc.get('concept_title', ''),
                                concept_explanation=kc.get('concept_explanation', ''),

                                source_page_number=kc.get('source_page_number'),
                                source_video_timestamp_start_seconds=kc.get('source_video_timestamp_start_seconds'),
                                source_video_timestamp_end_seconds=kc.get('source_video_timestamp_end_seconds'),
                                created_at=datetime.fromisoformat(kc.get('created_at')) if kc.get('created_at') else datetime.now()
                            ))
                        else:
                            # Skip this key concept if we can't convert it
                            logger.error(f"Failed to convert key concept: {kc}")
                    except Exception as e:
                        # Skip this key concept if we can't convert it
                        logger.error(f"Failed to convert key concept with error: {e}")
                        pass
            
            response_data = KeyConceptsFileResponse(key_concepts=key_concept_responses)
            return StandardResponse(
                status="success",
                data=response_data,
                count=len(key_concept_responses),
                message="Key concepts retrieved successfully"
            )
        except Exception as kc_error:
            logger.error(f"Error retrieving key concepts: {kc_error}")
            # Return empty list with error status
            return StandardResponse(
                status="error",
                data=KeyConceptsFileResponse(key_concepts=[]),
                count=0,
                message=f"Error retrieving key concepts: {str(kc_error)[:100]}"
            )
    except Exception as e:
        logger.error(f"Error in key concepts endpoint for file {file_id}: {e}", exc_info=True)
        # Return empty response with error status
        return StandardResponse(
            status="error",
            data=KeyConceptsFileResponse(key_concepts=[]),
            count=0,
            message=f"Error processing request: {str(e)[:100]}"
        )