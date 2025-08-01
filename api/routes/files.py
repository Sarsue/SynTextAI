import os
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile, Request, Response, status, Query, Path, BackgroundTasks
from typing import List, Dict, Optional, Any, TypeVar
from sqlalchemy.orm import Session, joinedload
from redis.exceptions import RedisError
from ..utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from ..repositories.repository_manager import RepositoryManager
from fastapi.responses import JSONResponse
from ..dependencies import get_store, authenticate_user
from ..services.agent_service import agent_service
from pydantic import BaseModel, Field
from ..schemas.learning_content import (
    StandardResponse,
    KeyConceptCreate, KeyConceptResponse, KeyConceptsListResponse, KeyConceptUpdate,
    FlashcardCreate, FlashcardResponse, FlashcardsListResponse, FlashcardUpdateRequest,
    QuizQuestionCreate, QuizQuestionResponse, QuizQuestionsListResponse, QuizQuestionUpdate,
    KeyConceptUpdateRequest
)
from ..models import KeyConcept as KeyConceptORM, Flashcard as FlashcardORM, QuizQuestion as QuizQuestionORM, File

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

async def process_file_with_ingestion_agent(
    file_id: int,
    file_path: str,
    file_type: str,
    language: str,
    comprehension_level: str,
    user_id: str,
    store: RepositoryManager
):
    """Process an uploaded file using the IngestionAgent."""
    try:
        # Use the IngestionAgent to process the file
        result = await agent_service.process_content(
            agent_name="ingestion",
            content={
                "file_path": file_path,
                "file_type": file_type,
                "language": language,
                "comprehension_level": comprehension_level,
                "user_id": user_id
            },
            content_type="json"
        )
        
        # Update file status to processed
        store.update_file(file_id, {"status": "processed"})
        
        # Store the processing results (key concepts, etc.)
        if result.get("key_concepts"):
            for concept in result["key_concepts"]:
                store.add_key_concept(
                    file_id=file_id,
                    title=concept["title"],
                    explanation=concept["explanation"],
                    source_page=concept.get("page"),
                    source_timestamp_start=concept.get("timestamp_start"),
                    source_timestamp_end=concept.get("timestamp_end")
                )
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing file with IngestionAgent: {e}", exc_info=True)
        store.update_file(file_id, {"status": "failed", "error": str(e)})
        raise

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
async def save_file(
    request: Request,
    background_tasks: BackgroundTasks,
    language: str = Query(default="en", description="Language code (e.g., 'en', 'es')"),
    comprehension_level: str = Query(default="beginner", description="Comprehension level (beginner, intermediate, advanced)"),
    files: Optional[List[UploadFile]] = FastAPIFile(None, description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Handle file uploads and process them using the IngestionAgent."""
    try:
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        
        content_type = request.headers.get("content-type", "")
        
        # Handle YouTube URL
        if content_type.startswith("application/json"):
            data = await request.json()
            if not (data and isinstance(data, dict) and data.get("type") == "youtube"):
                raise HTTPException(status_code=400, detail="Invalid payload for YouTube link upload.")
                
            url = data.get("url", "")
            youtube_regex = re.compile(r"^(https?://)?(www\\.)?(youtube\\.com|youtu\\.be)/")
            if not url or not youtube_regex.match(url):
                raise HTTPException(status_code=400, detail="Invalid YouTube URL.")
            
            # Create file record
            file_record = store.add_file(
                user_id=user_id,
                file_name=url,
                file_url=url,
                file_type="youtube",
                status="processing"
            )
            
            if not file_record:
                raise HTTPException(status_code=500, detail="Failed to create file record for YouTube URL.")
            
            # Process in background
            background_tasks.add_task(
                process_file_with_ingestion_agent,
                file_id=file_record["id"],
                file_path=url,
                file_type="youtube",
                language=language,
                comprehension_level=comprehension_level,
                user_id=user_id,
                store=store
            )
            
            return UploadResponse(
                message="YouTube URL processing started",
                file_id=file_record["id"],
                file_name=url
            )
            
        # Handle file upload
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
async def get_flashcards_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Get all flashcards for a specific file using the FlashcardAgent.
    
    - **file_id**: The ID of the file to get flashcards for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting flashcards for file {file_id} (page {page}, size {page_size})")
        
        # Check file ownership
        check_ownership(file_id, user_data["user_id"], store)
        
        # Get file details for context
        file = store.get_file_by_id(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Use FlashcardAgent to get or generate flashcards
        flashcard_data = await agent_service.process_content(
            agent_name="flashcard",
            content={
                "action": "get_flashcards",
                "file_id": file_id,
                "file_content": file.get('content', ''),
                "key_concepts": [kc.dict() for kc in store.learning_material_repo.get_key_concepts_for_file(file_id)]
            },
            content_type="json"
        )
        
        # Convert flashcard data to response format
        flashcard_responses = [
            FlashcardResponse(
                id=card.get('id', idx),
                file_id=file_id,
                question=card['question'],
                answer=card['answer'],
                key_concept_id=card.get('key_concept_id'),
                is_custom=False,
                created_at=datetime.utcnow(),
                difficulty=card.get('difficulty', 'medium')
            ) for idx, card in enumerate(flashcard_data.get('flashcards', []), 1)
        ]
        
        return StandardResponse(
            data={"flashcards": [card.dict() for card in flashcard_responses]},
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

@files_router.post("/{file_id}/flashcards/generate", status_code=status.HTTP_200_OK, response_model=StandardResponse[FlashcardsListResponse])
async def generate_flashcards(
    file_id: int,
    count: int = Query(5, ge=1, le=20, description="Number of flashcards to generate"),
    difficulty: str = Query("medium", description="Difficulty level (easy, medium, hard)"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Generate flashcards for a file using the FlashcardAgent.
    
    - **file_id**: The ID of the file to generate flashcards for
    - **count**: Number of flashcards to generate (1-20)
    - **difficulty**: Difficulty level of flashcards
    """
    try:
        logger.info(f"[API] Generating {count} flashcards for file {file_id}")
        
        # Check file ownership
        check_ownership(file_id, user_data["user_id"], store)
        
        # Get file and key concepts for context
        file = store.get_file_by_id(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
            
        key_concepts = [
            {"id": kc.id, "title": kc.title, "explanation": kc.explanation}
            for kc in store.learning_material_repo.get_key_concepts_for_file(file_id)
        ]
        
        # Use FlashcardAgent to generate flashcards
        flashcard_data = await agent_service.process_content(
            agent_name="flashcard",
            content={
                "action": "generate_flashcards",
                "file_id": file_id,
                "file_content": file.get('content', ''),
                "key_concepts": key_concepts,
                "count": count,
                "difficulty": difficulty
            },
            content_type="json"
        )
        
        # Store generated flashcards
        saved_flashcards = []
        for card in flashcard_data.get('flashcards', []):
            flashcard = store.learning_material_repo.add_flashcard(
                file_id=file_id,
                user_id=user_data["user_id"],
                question=card['question'],
                answer=card['answer'],
                key_concept_id=card.get('key_concept_id'),
                is_custom=False,
                difficulty=card.get('difficulty', difficulty)
            )
            if flashcard:
                saved_flashcards.append(flashcard)
        
        # Convert to response models
        flashcard_responses = [
            FlashcardResponse(
                id=card['id'],
                file_id=card['file_id'],
                question=card['question'],
                answer=card['answer'],
                key_concept_id=card.get('key_concept_id'),
                is_custom=False,
                created_at=card['created_at'],
                difficulty=card.get('difficulty', 'medium')
            ) for card in saved_flashcards
        ]
        
        return StandardResponse(
            data={"flashcards": [card.dict() for card in flashcard_responses]},
            message=f"Successfully generated {len(flashcard_responses)} flashcards"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating flashcards for file {file_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate flashcards"
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
        
        # Check file ownership and processing status
        check_ownership(file_id, user_data["user_id"], store)
        
        # Create a dictionary with the flashcard data to pass to the repository
        flashcard_dict = flashcard_data.dict()
        
        # Add the flashcard and get the new flashcard ID
        flashcard_id = store.learning_material_repo.add_flashcard(
            file_id=file_id,
            flashcard_data=flashcard_data
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
        
        # Check file ownership first
        check_ownership(file_id, user_data["user_id"], store)
        
        # Update the flashcard - the repository will verify it belongs to the user
        updated_flashcard = store.learning_material_repo.update_flashcard(
            flashcard_id=flashcard_id,
            user_id=user_data["user_id"],
            update_data=flashcard_update_data
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
        
        # Check file ownership first
        check_ownership(file_id, user_data["user_id"], store)
        
        # Delete the flashcard - the repository will verify it belongs to the user
        success = store.learning_material_repo.delete_flashcard(
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
            detail="An error occurred while deleting the flashcard"
        )

@files_router.get(
    "/{file_id}/quiz-questions",
    response_model=StandardResponse[QuizQuestionsListResponse],
    tags=["Quiz Questions"]
)
async def get_quiz_questions_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Get all quiz questions for a specific file.
    
    - **file_id**: The ID of the file to get quiz questions for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting quiz questions for file {file_id} (page {page}, size {page_size})")
        
        # Check file ownership
        check_ownership(file_id, user_data["user_id"], store)
        
        # Get file details for context
        file = store.get_file_by_id(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Use QuizAgent to generate or retrieve questions
        quiz_data = await agent_service.process_content(
            agent_name="quiz",
            content={
                "action": "get_questions",
                "file_id": file_id,
                "file_content": file.get('content', ''),
                "key_concepts": [kc.dict() for kc in store.learning_material_repo.get_key_concepts_for_file(file_id)]
            },
            content_type="json"
        )
        
        # Convert quiz data to response format
        question_responses = [
            QuizQuestionResponse(
                id=q.get('id', idx),
                file_id=file_id,
                question=q['question'],
                distractors=q.get('distractors', []),
                correct_answer=q['correct_answer'],
                explanation=q.get('explanation', ''),
                key_concept_id=q.get('key_concept_id'),
                is_custom=False,
                created_at=datetime.utcnow(),
                difficulty=q.get('difficulty', 'medium'),
                question_type=q.get('question_type', 'MCQ')
            ) for idx, q in enumerate(quiz_data.get('questions', []), 1)
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

@files_router.post("/{file_id}/quizzes/generate", status_code=status.HTTP_200_OK, response_model=StandardResponse[QuizQuestionsListResponse])
async def generate_quiz_questions(
    file_id: int,
    count: int = Query(5, ge=1, le=10, description="Number of questions to generate"),
    difficulty: str = Query("medium", description="Difficulty level (easy, medium, hard)"),
    question_types: List[str] = Query(["MCQ"], description="Types of questions to generate"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Generate quiz questions for a file using the QuizAgent.
    
    - **file_id**: The ID of the file to generate questions for
    - **count**: Number of questions to generate (1-10)
    - **difficulty**: Difficulty level of questions
    - **question_types**: Types of questions to generate (MCQ, True/False, etc.)
    """
    try:
        logger.info(f"[API] Generating {count} quiz questions for file {file_id}")
        
        # Check file ownership
        check_ownership(file_id, user_data["user_id"], store)
        
        # Get file and key concepts for context
        file = store.get_file_by_id(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
            
        key_concepts = [
            {"id": kc.id, "title": kc.title, "explanation": kc.explanation}
            for kc in store.learning_material_repo.get_key_concepts_for_file(file_id)
        ]
        
        # Use QuizAgent to generate questions
        quiz_data = await agent_service.process_content(
            agent_name="quiz",
            content={
                "action": "generate_questions",
                "file_id": file_id,
                "file_content": file.get('content', ''),
                "key_concepts": key_concepts,
                "count": count,
                "difficulty": difficulty,
                "question_types": question_types
            },
            content_type="json"
        )
        
        # Store generated questions
        saved_questions = []
        for q in quiz_data.get('questions', []):
            question = store.learning_material_repo.add_quiz_question(
                file_id=file_id,
                user_id=user_data["user_id"],
                question=q['question'],
                distractors=q.get('distractors', []),
                correct_answer=q['correct_answer'],
                explanation=q.get('explanation', ''),
                key_concept_id=q.get('key_concept_id'),
                is_custom=False,
                difficulty=q.get('difficulty', difficulty),
                question_type=q.get('question_type', 'MCQ')
            )
            if question:
                saved_questions.append(question)
        
        # Convert to response models
        question_responses = [
            QuizQuestionResponse(
                id=q['id'],
                file_id=q['file_id'],
                question=q['question'],
                distractors=q.get('distractors', []),
                correct_answer=q['correct_answer'],
                explanation=q.get('explanation', ''),
                key_concept_id=q.get('key_concept_id'),
                is_custom=False,
                created_at=q['created_at'],
                difficulty=q.get('difficulty', 'medium'),
                question_type=q.get('question_type', 'MCQ')
            ) for q in saved_questions
        ]
        
        return StandardResponse(
            data={"quizzes": [q.dict() for q in question_responses]},
            message=f"Successfully generated {len(question_responses)} quiz questions"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating quiz questions for file {file_id}: {e}", exc_info=True)
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
        
        # Check file ownership first
        check_ownership(file_id, user_data["user_id"], store)
        
        # Update the quiz question - the repository will verify it belongs to the user
        updated_question = store.learning_material_repo.update_quiz_question(
            quiz_question_id=quiz_question_id,
            user_id=user_data["user_id"],
            update_data=quiz_question_data
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
        
        # Check file ownership first
        check_ownership(file_id, user_data["user_id"], store)
        
        # Delete the quiz question - the repository will verify it belongs to the user
        success = store.learning_material_repo.delete_quiz_question(
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

@files_router.post(
    "/{file_id}/key-concepts",
    status_code=status.HTTP_201_CREATED,
    response_model=StandardResponse[KeyConceptResponse],
    tags=["Key Concepts"]
)
async def add_key_concept(
    file_id: int,
    key_concept_data: KeyConceptCreate,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse[KeyConceptResponse]:
    """
    Add a new key concept to a file.
    
    - **file_id**: The ID of the file to add the key concept to
    - **key_concept_data**: The key concept data to add
    """
    try:
        logger.info(f"[API] Adding key concept to file {file_id}")
        
        # Verify file ownership through repository
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
        # Add the key concept through the repository
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
            detail="An unexpected error occurred while adding the key concept"
        )

@files_router.get(
    "/{file_id}/key-concepts",
    response_model=StandardResponse[KeyConceptsListResponse],
    tags=["Key Concepts"]
)
async def get_key_concepts_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse[KeyConceptsListResponse]:
    """
    Get all key concepts for a specific file.
    
    - **file_id**: The ID of the file to get key concepts for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting key concepts for file {file_id}")
        
        # Check file ownership
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
            
        # Get paginated key concepts
        concepts = store.learning_material_repo.get_key_concepts_for_file(
            file_id=file_id,
            page=page,
            page_size=page_size
        )
        
        # Convert to response models with frontend-compatible format
        concept_responses = [
            {
                "id": concept["id"],
                "file_id": concept["file_id"],
                "concept_title": concept["concept_title"],
                "concept_explanation": concept["concept_explanation"],
                "display_order": None,  # Frontend expects this field
                "source_page_number": concept["source_page_number"],
                "source_video_timestamp_start_seconds": concept["source_video_timestamp_start_seconds"],
                "source_video_timestamp_end_seconds": concept["source_video_timestamp_end_seconds"],
                "is_custom": concept["is_custom"],
                "created_at": concept["created_at"].isoformat() if concept["created_at"] else None,
                "updated_at": concept["updated_at"].isoformat() if concept["updated_at"] else None
            } for concept in concepts
        ]
        
        logger.info(f"[API] Successfully retrieved {len(concept_responses)} key concepts for file {file_id}")
        return StandardResponse(
            data={"key_concepts": [c.model_dump() for c in concept_responses]},
            message="Key concepts retrieved successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting key concepts: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving key concepts"
        )

@files_router.get("/{file_id}/quizzes", response_model=StandardResponse)
async def get_quizzes(
    file_id: int = Path(..., gt=0, description="The ID of the file to retrieve quizzes for"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        logger.info(f"[API] Adding key concept for file {file_id} by user {user_data['user_id']}")
        
        # Verify file ownership through repository
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
        # Add the key concept through the repository
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
        # Verify file ownership through repository
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
        # Update the key concept
        updated_concept = store.learning_material_repo.update_key_concept(
            concept_id=key_concept_id,
            update_data=key_concept_update_data
        )
        
        if not updated_concept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key concept not found or doesn't belong to the specified file."
            )
        
        # Convert to frontend-compatible response format
        response_data = {
            "id": updated_concept['id'],
            "file_id": updated_concept['file_id'],
            "concept_title": updated_concept['concept_title'],
            "concept_explanation": updated_concept['concept_explanation'],
            "display_order": None,  # Frontend expects this field
            "source_page_number": updated_concept['source_page_number'],
            "source_video_timestamp_start_seconds": updated_concept['source_video_timestamp_start_seconds'],
            "source_video_timestamp_end_seconds": updated_concept['source_video_timestamp_end_seconds'],
            "is_custom": updated_concept['is_custom'],
            "created_at": updated_concept['created_at'].isoformat() if updated_concept['created_at'] else None,
            "updated_at": updated_concept['updated_at'].isoformat() if updated_concept['updated_at'] else None
        }
        
        return StandardResponse(
            data=response_data,
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
    check_ownership(file_id, user_data["user_id"], store)
    success = store.learning_material_repo.delete_key_concept(
        key_concept_id=key_concept_id, 
        user_id=user_data["user_id"]
    )
    if not success:
        raise HTTPException(status_code=404, detail="Key concept not found or user does not have permission.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)