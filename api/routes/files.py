import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Response, status, Query, Path
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
    KeyConceptCreate, KeyConceptResponse, KeyConceptsListResponse, KeyConceptUpdateRequest,
    FlashcardCreate, FlashcardResponse, FlashcardsListResponse, FlashcardUpdateRequest,
    QuizQuestionCreate, QuizQuestionResponse, QuizQuestionsListResponse, QuizQuestionUpdateRequest
)

class UploadResponse(BaseModel):
    message: str
    file_id: Optional[int] = None
    file_name: Optional[str] = None
    job_id: Optional[str] = None

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
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
async def save_file(
    request: Request,
    language: str = Query(default="English", description="Language of the file content"),
    comprehension_level: str = Query(default="Beginner", description="Comprehension level of the file content"),
    files: Optional[List[UploadFile]] = File(None, description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]

        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            data = await request.json()
            if not (data and isinstance(data, dict) and data.get("type") == "youtube"):
                raise HTTPException(status_code=400, detail="Invalid payload for YouTube link upload.")
            url = data.get("url", "")

            youtube_regex = re.compile(r"^(https?://)?(www\\.)?(youtube\\.com|youtu\\.be)/")
            if not url or not youtube_regex.match(url):
                raise HTTPException(status_code=400, detail="Invalid YouTube URL.")
            
            file_record = store.add_file(user_id=user_id, file_name=url, file_url=url)
            if not file_record:
                raise HTTPException(status_code=500, detail="Failed to create file record for YouTube URL.")

            task_name = 'tasks.process_youtube_url'
            request.app.state.celery_app.send_task(task_name, args=[file_record['id'], url, language, comprehension_level])
            return UploadResponse(files=[FileResponse.model_validate(file_record)])

        elif content_type.startswith("multipart/form-data"):
            if not files:
                raise HTTPException(status_code=400, detail="No files were uploaded.")
            
            uploaded_files_responses = []
            for file in files:
                gcs_url = upload_to_gcs(file, user_gc_id)
                file_record = store.add_file(user_id=user_id, file_name=file.filename, file_url=gcs_url)
                if not file_record:
                    logger.error(f"Failed to create file record for {file.filename}")
                    continue

                task_name = 'tasks.process_uploaded_file'
                request.app.state.celery_app.send_task(task_name, args=[file_record['id'], gcs_url, file.filename, language, comprehension_level])
                uploaded_files_responses.append(FileResponse.model_validate(file_record))
            return UploadResponse(files=uploaded_files_responses)
        else:
            raise HTTPException(status_code=400, detail="Unsupported Content-Type.")

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
        paginated_result = store.file_repo.get_files_for_user(user_id, skip=offset, limit=page_size)
        
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

        file_to_delete = store.file_repo.get_file_by_id(file_id)
        if not file_to_delete or file_to_delete.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized.")

        if file_to_delete.file_url and "storage.googleapis.com" in file_to_delete.file_url:
             delete_from_gcs(file_to_delete.file_url, user_gc_id)

        if not store.delete_file_entry(file_id=file_id, user_id=user_id):
             raise HTTPException(status_code=500, detail="Failed to delete file entry.")

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete file.")


# --- Learning Content Endpoints ---

# Helper for ownership check
def check_ownership(file_id: int, user_id: int, store: RepositoryManager):
    if not store.file_repo.check_user_file_ownership(file_id, user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized.")

# --- Flashcards ---

@files_router.get("/{file_id}/flashcards", response_model=StandardResponse)
async def get_flashcards_for_file(file_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)) -> StandardResponse:
    check_ownership(file_id, user_data["user_id"], store)
    flashcards = store.learning_material_repo.get_flashcards_for_file(file_id)
    return StandardResponse(data=FlashcardsListResponse(flashcards=flashcards), message="Flashcards retrieved successfully")

@files_router.post("/{file_id}/flashcards", response_model=StandardResponse[FlashcardResponse], status_code=status.HTTP_201_CREATED)
async def add_flashcard_for_file(file_id: int, flashcard_data: FlashcardCreate, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    check_ownership(file_id, user_data["user_id"], store)
    
    # Create a new unit of work context
    with store.get_unit_of_work() as uow:
        try:
            # Add the flashcard within the session context
            new_flashcard = store.learning_material_repo.add_flashcard(file_id=file_id, flashcard_data=flashcard_data)
            if not new_flashcard:
                raise HTTPException(status_code=500, detail="Failed to create flashcard.")
            
            # Refresh to ensure we have all fields
            uow.session.refresh(new_flashcard)
            
            # Create a dictionary with all the required fields before the session closes
            flashcard_dict = {
                "id": new_flashcard.id,
                "question": new_flashcard.question,
                "answer": new_flashcard.answer,
                "file_id": new_flashcard.file_id,
                "key_concept_id": new_flashcard.key_concept_id,
                "is_custom": new_flashcard.is_custom,
                "created_at": new_flashcard.created_at,
                "updated_at": new_flashcard.updated_at
            }
            
            return StandardResponse(
                data=FlashcardResponse.model_validate(flashcard_dict), 
                message="Flashcard added successfully."
            )
            
        except Exception as e:
            uow.session.rollback()
            logger.error(f"Error adding flashcard: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to create flashcard: {str(e)}")

@files_router.put("/flashcards/{flashcard_id}", response_model=StandardResponse[FlashcardResponse])
async def update_flashcard(flashcard_id: int, flashcard_update_data: FlashcardUpdateRequest, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    # Update the flashcard - the repository method returns a dictionary with the updated data
    updated_data = store.learning_material_repo.update_flashcard(
        flashcard_id=flashcard_id,
        user_id=user_data["user_id"], 
        update_data=flashcard_update_data
    )
    
    if not updated_data:
        raise HTTPException(status_code=404, detail="Flashcard not found or user does not have permission.")
    
    # Return the response with the updated flashcard data
    return StandardResponse(
        data=FlashcardResponse.model_validate(updated_data), 
        message="Flashcard updated successfully."
    )

@files_router.delete("/flashcards/{flashcard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flashcard(flashcard_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    success = store.learning_material_repo.delete_flashcard(flashcard_id=flashcard_id, user_id=user_data["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Flashcard not found or user does not have permission.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# --- Quiz Questions ---

@files_router.get("/{file_id}/quizzes", response_model=StandardResponse)
async def get_quiz_questions_for_file(file_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)) -> StandardResponse:
    check_ownership(file_id, user_data["user_id"], store)
    questions = store.learning_material_repo.get_quiz_questions_for_file(file_id)
    # Wrap the questions in QuizQuestionsListResponse and convert to dict
    return StandardResponse(
        data=QuizQuestionsListResponse(quizzes=questions).model_dump(),
        message="Quizzes retrieved successfully"
    )

@files_router.post("/{file_id}/quizzes", response_model=StandardResponse[QuizQuestionResponse], status_code=status.HTTP_201_CREATED)
async def add_quiz_question_for_file(
    quiz_question_data: QuizQuestionCreate,
    file_id: int = Path(..., gt=0, description="The ID of the file to add the quiz question to"),
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
):
    """
    Add a new quiz question to a file.
    
    - **file_id**: The ID of the file to add the quiz question to (must be a positive integer)
    - **quiz_question_data**: The quiz question data to add
    """
    try:
        logger.info(f"[API] Adding quiz question for file {file_id} by user {user_data['user_id']}")
        
        # Check ownership and validate input
        check_ownership(file_id, user_data["user_id"], store)
        
        # Log the incoming data for debugging (excluding any sensitive data)
        log_data = quiz_question_data.dict()
        if 'correct_answer' in log_data:
            log_data['correct_answer'] = '[REDACTED]' if log_data['correct_answer'] else None
        logger.debug(f"[API] Quiz question data: {log_data}")
        
        # Validate required fields
        if not quiz_question_data.question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Question is a required field."
            )
        if not quiz_question_data.correct_answer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Correct answer is a required field."
            )
            
        try:
            # Add the quiz question
            new_question = store.learning_material_repo.add_quiz_question(
                file_id=file_id, 
                quiz_question_data=quiz_question_data
            )
            
            if not new_question:
                logger.error(f"[API] Failed to create quiz question for file {file_id}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                    detail="Failed to create quiz question in the database."
                )
                
            logger.info(f"[API] Successfully created quiz question {new_question.id} for file {file_id}")
            return StandardResponse(
                data=QuizQuestionResponse.model_validate(new_question), 
                message="Quiz question added successfully."
            )
            
        except ValueError as ve:
            # Handle validation errors from the repository
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(ve)
            )
        except Exception as e:
            # Log the full error for debugging
            logger.error(f"[API] Database error adding quiz question: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="A database error occurred while adding the quiz question."
            )
            
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"[API] Unexpected error adding quiz question: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while adding the quiz question."
        )

@files_router.put("/quizzes/{quiz_question_id}", response_model=StandardResponse[QuizQuestionResponse])
async def update_quiz_question(quiz_question_id: int, quiz_question_update_data: QuizQuestionUpdateRequest, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    updated_question = store.learning_material_repo.update_quiz_question(quiz_question_id=quiz_question_id, user_id=user_data["user_id"], update_data=quiz_question_update_data)
    if not updated_question:
        raise HTTPException(status_code=404, detail="Quiz question not found or user does not have permission.")
    return StandardResponse(data=QuizQuestionResponse.model_validate(updated_question), message="Quiz question updated successfully.")

@files_router.delete("/quizzes/{quiz_question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quiz_question(quiz_question_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    success = store.learning_material_repo.delete_quiz_question(quiz_question_id=quiz_question_id, user_id=user_data["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Quiz question not found or user does not have permission.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# --- Key Concepts ---

@files_router.get("/{file_id}/key-concepts", response_model=StandardResponse)
async def get_key_concepts_for_file(file_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)) -> StandardResponse:
    check_ownership(file_id, user_data["user_id"], store)
    concepts = store.learning_material_repo.get_key_concepts_for_file(file_id)
    # Wrap the concepts in KeyConceptsListResponse and convert to dict
    return StandardResponse(
        data=KeyConceptsListResponse(key_concepts=concepts).model_dump(),
        message="Key concepts retrieved successfully"
    )

@files_router.post("/{file_id}/key-concepts", response_model=StandardResponse[KeyConceptResponse], status_code=status.HTTP_201_CREATED)
async def add_key_concept_for_file(
    key_concept_data: KeyConceptCreate,
    file_id: int = Path(..., gt=0, description="The ID of the file to add the key concept to"), 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
):
    """
    Add a new key concept to a file.
    
    - **file_id**: The ID of the file to add the key concept to (must be a positive integer)
    - **key_concept_data**: The key concept data to add
    """
    try:
        logger.info(f"[API] Adding key concept for file {file_id} by user {user_data['user_id']}")
        
        # Check ownership and validate input
        check_ownership(file_id, user_data["user_id"], store)
        
        # Log the incoming data for debugging (excluding any sensitive data)
        log_data = key_concept_data.dict()
        if 'explanation' in log_data:
            log_data['explanation'] = '[REDACTED]' if log_data['explanation'] else None
        logger.debug(f"[API] Key concept data: {log_data}")
        
        try:
            # Add the key concept
            new_concept = store.learning_material_repo.add_key_concept(
                file_id=file_id, 
                key_concept_data=key_concept_data
            )
            
            if not new_concept:
                logger.error(f"[API] Failed to create key concept for file {file_id}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                    detail="Failed to create key concept in the database."
                )
                
            logger.info(f"[API] Successfully created key concept {new_concept.id} for file {file_id}")
            return StandardResponse(
                data=KeyConceptResponse.model_validate(new_concept), 
                message="Key concept added successfully."
            )
            
        except ValueError as ve:
            # Handle validation errors from the repository
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(ve)
            )
        except Exception as e:
            # Log the full error for debugging
            logger.error(f"[API] Database error adding key concept: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="A database error occurred while adding the key concept."
            )
            
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"[API] Unexpected error adding key concept: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while adding the key concept."
        )

@files_router.put("/key-concepts/{key_concept_id}", response_model=StandardResponse[KeyConceptResponse])
async def update_key_concept(key_concept_id: int, key_concept_update_data: KeyConceptUpdateRequest, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    updated_concept = store.learning_material_repo.update_key_concept(key_concept_id=key_concept_id, user_id=user_data["user_id"], update_data=key_concept_update_data)
    if not updated_concept:
        raise HTTPException(status_code=404, detail="Key concept not found or user does not have permission.")
    return StandardResponse(data=KeyConceptResponse.model_validate(updated_concept), message="Key concept updated successfully.")

@files_router.delete("/key-concepts/{key_concept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key_concept(key_concept_id: int, user_data: Dict = Depends(authenticate_user), store: RepositoryManager = Depends(get_store)):
    success = store.learning_material_repo.delete_key_concept(key_concept_id=key_concept_id, user_id=user_data["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Key concept not found or user does not have permission.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)