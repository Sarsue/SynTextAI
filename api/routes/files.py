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

class FileUploadResponse(BaseModel):
    id: int
    file_name: str
    file_url: str
    status: str

class UploadResponse(BaseModel):
    message: str
    files: List[FileUploadResponse] = []
    file_id: Optional[int] = None  # For backward compatibility
    file_name: Optional[str] = None  # For backward compatibility
    job_id: Optional[str] = None  # For backward compatibility

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
    language: str = Query("en", description="Language code (e.g., 'en', 'es')"),
    comprehension_level: str = Query("beginner", description="Comprehension level (beginner, intermediate, advanced)"),
    files: List[UploadFile] = FastAPIFile(..., description="List of files to upload"),
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
                
            url = data.get("url", "").strip()
            # Support formats:
            # - https://www.youtube.com/watch?v=...
            # - https://youtu.be/...
            # - www.youtube.com/watch?v=...
            # - youtube.com/watch?v=...
            # - youtu.be/...
            youtube_regex = re.compile(
                r'^(https?://)?(www\.)?'
                r'(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/e/|youtube\.com/\?v=)'
                r'([^&\n?#]+)'
            )
            
            if not url or not youtube_regex.search(url):
                logger.error(f"Invalid YouTube URL: {url}")
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid YouTube URL. Please use a valid YouTube URL (e.g., https://www.youtube.com/watch?v=... or https://youtu.be/...)"
                )
            
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
                process_file_data,
                user_gc_id=user_gc_id,
                user_id=user_id,
                file_id=file_record["id"],
                filename=url,
                file_url=url,
                is_youtube=True,  # Mark as YouTube video
                language=language,
                comprehension_level=comprehension_level
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
                # Upload file to GCS
                gcs_url = await upload_to_gcs(file, user_gc_id, file.filename)
                if not gcs_url:
                    logger.error(f"Failed to upload file to GCS: {file.filename}")
                    continue

                # Add file record to database
                file_record = store.file_repo.add_file(
                    user_id=user_id,
                    file_name=file.filename,
                    file_url=gcs_url,
                    file_type=file.content_type or 'application/octet-stream',
                    status='processing'
                )
                
                if not file_record:
                    logger.error(f"Failed to create file record for {file.filename}")
                    continue

                # Start background processing
                task_name = 'tasks.process_uploaded_file'
                request.app.state.celery_app.send_task(
                    task_name, 
                    args=[user_gc_id, user_id, file_record, gcs_url, file.filename, language, comprehension_level]
                )
                
                # Add to response
                file_response = FileUploadResponse(
                    id=file_record,
                    file_name=file.filename,
                    file_url=gcs_url,
                    status='processing'
                )
                uploaded_files_responses.append(file_response)
                
            response = UploadResponse(
                message=f"Successfully uploaded {len(uploaded_files_responses)} files",
                files=uploaded_files_responses
            )
            
            # For backward compatibility, set the first file's info at the top level
            if uploaded_files_responses:
                response.file_id = uploaded_files_responses[0].id
                response.file_name = uploaded_files_responses[0].file_name
                
            return response
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
        if not file_to_delete or file_to_delete.get('user_id') != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found or unauthorized.")

        file_url = file_to_delete.get('file_url')
        if file_url and "storage.googleapis.com" in file_url:
             delete_from_gcs(file_url, user_gc_id)

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

@files_router.get("/{file_id}/flashcards", response_model=dict)
async def get_flashcards_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
) -> dict:
    """
    Get all flashcards for a specific file using the QuizAgent.
    
    - **file_id**: The ID of the file to get flashcards for
    - **page**: Page number for pagination (default: 1)
    - **page_size**: Number of items per page (default: 10, max: 100)
    """
    try:
        logger.info(f"[API] Getting flashcards for file {file_id} (page {page}, size {page_size})")
        
        # Check file ownership
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
        # Get file details for context
        file = store.get_file_by_id(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Get existing flashcards from the database
        db_flashcards = store.learning_material_repo.get_flashcards_for_file(
            file_id=file_id,
            page=page,
            page_size=page_size
        )
        
        # If no flashcards exist, return empty list (frontend will handle generation)
        if not db_flashcards:
            logger.info(f"No flashcards found for file {file_id}")
            return {
                "status": "success",
                "data": {
                    "flashcards": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size
                }
            }
        
        # Format the response with frontend-compatible structure
        formatted_flashcards = []
        for card in db_flashcards:
            formatted_card = {
                "id": card.id,
                "file_id": card.file_id,
                "question": card.question,
                "answer": card.answer,
                "key_concept_id": card.key_concept_id,
                "is_custom": getattr(card, 'is_custom', False),
                "difficulty": getattr(card, 'difficulty', 'medium'),
                "created_at": card.created_at.isoformat() if card.created_at else None,
                "updated_at": getattr(card, 'updated_at', None).isoformat() if hasattr(card, 'updated_at') and card.updated_at else None
            }
            formatted_flashcards.append(formatted_card)
        
        # Get total count for pagination
        total_count = store.learning_material_repo.count_flashcards_for_file(file_id=file_id)
        
        logger.info(f"[API] Successfully retrieved {len(formatted_flashcards)} flashcards for file {file_id}")
        return {
            "status": "success",
            "data": {
                "flashcards": formatted_flashcards,
                "total": total_count,
                "page": page,
                "page_size": page_size
            }
        }
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
    Generate flashcards for a file using the QuizAgent.
    
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
        
        # Get file content
        file_content = file.get('content', '')
        
        # Use QuizAgent to generate flashcards
        flashcard_data = await agent_service.process_content(
            agent_name="quiz",
            content=file_content,
            content_type="json",
            input_data={
                "action": "generate_flashcards",
                "file_id": file_id,
                "key_concepts": key_concepts,
                "count": count,
                "difficulty": difficulty
            }
        )
        
        # Store generated flashcards
        saved_flashcards = []
        for card in flashcard_data.get('flashcards', []):
            flashcard = store.learning_material_repo.add_flashcard(
                file_id=file_id,
                user_id=user_data["user_id"],
                question=card.question if hasattr(card, 'question') else card['question'],
                answer=card.answer if hasattr(card, 'answer') else card['answer'],
                key_concept_id=card.key_concept_id if hasattr(card, 'key_concept_id') else card.get('key_concept_id'),
                is_custom=False,
                difficulty=card.difficulty if hasattr(card, 'difficulty') else card.get('difficulty', difficulty)
            )
            if flashcard:
                saved_flashcards.append(flashcard)
        
        # Convert to response models
        flashcard_responses = [
            FlashcardResponse(
                id=card.id,
                file_id=card.file_id,
                question=card.question,
                answer=card.answer,
                key_concept_id=card.key_concept_id if hasattr(card, 'key_concept_id') else None,
                is_custom=getattr(card, 'is_custom', False),
                created_at=card.created_at,
                updated_at=getattr(card, 'updated_at', None),
                difficulty=getattr(card, 'difficulty', 'medium')
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

@files_router.post("/{file_id}/flashcards", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_flashcard_for_file(
    file_id: int, 
    flashcard_data: FlashcardCreate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> dict:
    """
    Add a new flashcard to a file.
    
    - **file_id**: The ID of the file to add the flashcard to
    - **flashcard_data**: The flashcard data to add
    """
    try:
        logger.info(f"[API] Adding flashcard to file {file_id} by user {user_data['user_id']}")
        
        # Check file ownership and processing status
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
        # Add the flashcard and get the new flashcard
        flashcard = store.learning_material_repo.add_flashcard(
            file_id=file_id,
            flashcard_data=flashcard_data
        )
        
        if not flashcard:
            logger.error(f"[API] Failed to create flashcard for file {file_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create flashcard in the database"
            )
        
        logger.info(f"[API] Successfully created flashcard {flashcard.get('id')} for file {file_id}")
        
        # Format the response - using dictionary access since we get a dict from the repository
        response_data = {
            "id": flashcard.get('id'),
            "file_id": flashcard.get('file_id'),
            "question": flashcard.get('question'),
            "answer": flashcard.get('answer'),
            "key_concept_id": flashcard.get('key_concept_id'),
            "is_custom": flashcard.get('is_custom', False),
            "difficulty": 'medium',  # Default value since it's not in the model
            "created_at": flashcard.get('created_at').isoformat() if flashcard.get('created_at') else None,
            "updated_at": flashcard.get('updated_at').isoformat() if flashcard.get('updated_at') else None
        }
        
        return {
            "status": "success",
            "data": response_data,
            "message": "Flashcard created successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding flashcard: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while adding the flashcard"
        )

@files_router.put("/{file_id}/flashcards/{flashcard_id}", response_model=dict)
async def update_flashcard(
    file_id: int,
    flashcard_id: int, 
    flashcard_update_data: FlashcardUpdateRequest, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> dict:
    """
    Update an existing flashcard.
    
    - **file_id**: The ID of the file that the flashcard belongs to
    - **flashcard_id**: The ID of the flashcard to update
    - **flashcard_data**: The updated flashcard data
    """
    try:
        logger.info(f"[API] Updating flashcard {flashcard_id} for file {file_id} by user {user_data['user_id']}")
        
        # Check file ownership first
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
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
        
        # Format the response - handle both dictionary and object responses
        response_data = {
            "id": updated_flashcard.get('id') if isinstance(updated_flashcard, dict) else updated_flashcard.id,
            "file_id": updated_flashcard.get('file_id') if isinstance(updated_flashcard, dict) else updated_flashcard.file_id,
            "question": updated_flashcard.get('question') if isinstance(updated_flashcard, dict) else updated_flashcard.question,
            "answer": updated_flashcard.get('answer') if isinstance(updated_flashcard, dict) else updated_flashcard.answer,
            "key_concept_id": updated_flashcard.get('key_concept_id') if isinstance(updated_flashcard, dict) else getattr(updated_flashcard, 'key_concept_id', None),
            "is_custom": updated_flashcard.get('is_custom', False) if isinstance(updated_flashcard, dict) else getattr(updated_flashcard, 'is_custom', False),
            "difficulty": updated_flashcard.get('difficulty', 'medium') if isinstance(updated_flashcard, dict) else getattr(updated_flashcard, 'difficulty', 'medium'),
            "created_at": (updated_flashcard['created_at'].isoformat() if updated_flashcard['created_at'] else None) 
                          if isinstance(updated_flashcard, dict) and 'created_at' in updated_flashcard 
                          else (updated_flashcard.created_at.isoformat() if updated_flashcard.created_at else None),
            "updated_at": (updated_flashcard['updated_at'].isoformat() if updated_flashcard['updated_at'] else None) 
                          if isinstance(updated_flashcard, dict) and 'updated_at' in updated_flashcard 
                          else (updated_flashcard.updated_at.isoformat() if hasattr(updated_flashcard, 'updated_at') and updated_flashcard.updated_at else None)
        }
        
        return {
            "status": "success",
            "data": response_data,
            "message": "Flashcard updated successfully"
        }
        
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

@files_router.delete("/{file_id}/flashcards/{flashcard_id}", response_model=dict)
async def delete_flashcard(
    file_id: int,
    flashcard_id: int,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
) -> dict:
    """
    Delete a flashcard.
    
    - **file_id**: The ID of the file that the flashcard belongs to
    - **flashcard_id**: The ID of the flashcard to delete
    """
    try:
        logger.info(f"[API] Deleting flashcard {flashcard_id} for file {file_id} by user {user_data['user_id']}")
        
        # Check file ownership first
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
        
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
        
        return {
            "status": "success",
            "message": "Flashcard deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting flashcard: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the flashcard"
        )

@files_router.post(
    "/{file_id}/quiz-questions",
    response_model=StandardResponse[QuizQuestionResponse],
    status_code=status.HTTP_201_CREATED,
    tags=["Quiz Questions"]
)
async def add_quiz_question_for_file(
    file_id: int, 
    question_data: QuizQuestionCreate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> StandardResponse[QuizQuestionResponse]:
    """
    Add a new quiz question to a file.
    
    - **file_id**: The ID of the file to add the quiz question to
    - **question_data**: The quiz question data to add
    """
    try:
        logger.info(f"[API] Adding quiz question to file {file_id} by user {user_data['user_id']}")
        
        # Check file ownership
        check_ownership(file_id, user_data["user_id"], store)
        
        # Create a dictionary with the question data to pass to the repository
        question_dict = question_data.dict()
        
        # Add the quiz question and get the new question ID
        question_id = store.learning_material_repo.add_quiz_question(
            file_id=file_id,
            quiz_question_data=question_data
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
        
        return StandardResponse[QuizQuestionResponse](
            data=response_data,
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
        
        # Get quiz questions from the database
        quiz_questions = store.learning_material_repo.get_quiz_questions_for_file(
            file_id=file_id,
            page=page,
            page_size=page_size
        )
        
        # Check if the result is a paginated object or a plain list
        has_pagination = hasattr(quiz_questions, 'items') and hasattr(quiz_questions, 'total')
        
        # Convert to response format
        questions_list = quiz_questions.items if has_pagination else quiz_questions
        
        question_responses = [
            QuizQuestionResponse(
                id=q.id,
                file_id=q.file_id,
                question=q.question,
                question_type=q.question_type,
                correct_answer=q.correct_answer,
                distractors=q.distractors if hasattr(q, 'distractors') else [],
                key_concept_id=q.key_concept_id if hasattr(q, 'key_concept_id') else None,
                is_custom=q.is_custom if hasattr(q, 'is_custom') else False,
                created_at=q.created_at if hasattr(q, 'created_at') else None
            ) for q in questions_list
        ]
        
        # Prepare response message
        total_questions = len(question_responses) if not has_pagination else quiz_questions.total
        message = f"Retrieved {len(question_responses)} of {total_questions} questions"
        
        logger.info(f"[API] Successfully retrieved {len(question_responses)} quiz questions for file {file_id}")
        return StandardResponse[QuizQuestionsListResponse](
            data=QuizQuestionsListResponse(quizzes=question_responses),
            message=message
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
        
        # Get file content
        file_content = file.get('content', '')
        
        # Use QuizAgent to generate questions
        quiz_data = await agent_service.process_content(
            agent_name="quiz",
            content=file_content,
            content_type="json",
            input_data={
                "action": "generate_questions",
                "file_id": file_id,
                "key_concepts": key_concepts,
                "count": count,
                "difficulty": difficulty,
                "question_types": question_types
            }
        )
        
        # Store generated questions
        saved_questions = []
        for q in quiz_data.get('questions', []):
            question = store.learning_material_repo.add_quiz_question(
                file_id=file_id,
                user_id=user_data["user_id"],
                question=q.question if hasattr(q, 'question') else q['question'],
                distractors=q.distractors if hasattr(q, 'distractors') else q.get('distractors', []),
                correct_answer=q.correct_answer if hasattr(q, 'correct_answer') else q['correct_answer'],
                explanation=q.explanation if hasattr(q, 'explanation') else q.get('explanation', ''),
                key_concept_id=q.key_concept_id if hasattr(q, 'key_concept_id') else q.get('key_concept_id'),
                is_custom=False,
                difficulty=q.difficulty if hasattr(q, 'difficulty') else q.get('difficulty', difficulty),
                question_type=q.question_type if hasattr(q, 'question_type') else q.get('question_type', 'MCQ')
            )
            if question:
                saved_questions.append(question)
        
        # Convert to response models
        question_responses = [
            QuizQuestionResponse(
                id=q.id,
                file_id=q.file_id,
                question=q.question,
                distractors=getattr(q, 'distractors', []),
                correct_answer=q.correct_answer,
                explanation=getattr(q, 'explanation', ''),
                key_concept_id=q.key_concept_id if hasattr(q, 'key_concept_id') else None,
                is_custom=getattr(q, 'is_custom', False),
                created_at=q.created_at,
                updated_at=getattr(q, 'updated_at', None),
                difficulty=getattr(q, 'difficulty', 'medium'),
                question_type=getattr(q, 'question_type', 'MCQ')
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
        
        # Log success with the correct ID (either from the updated object or the known ID)
        updated_id = updated_question.get('id') if isinstance(updated_question, dict) else getattr(updated_question, 'id', quiz_question_id)
        logger.info(f"[API] Successfully updated quiz question {updated_id} for file {file_id}")
        
        # Convert to response model - handle both dictionary and object responses
        response_data = QuizQuestionResponse(
            id=updated_question.get('id') if isinstance(updated_question, dict) else updated_question.id,
            file_id=updated_question.get('file_id') if isinstance(updated_question, dict) else updated_question.file_id,
            question=updated_question.get('question') if isinstance(updated_question, dict) else updated_question.question,
            distractors=updated_question.get('distractors', []) if isinstance(updated_question, dict) else getattr(updated_question, 'distractors', []) or [],
            correct_answer=updated_question.get('correct_answer') if isinstance(updated_question, dict) else updated_question.correct_answer,
            question_type=updated_question.get('question_type', 'MCQ') if isinstance(updated_question, dict) else getattr(updated_question, 'question_type', 'MCQ'),
            key_concept_id=updated_question.get('key_concept_id') if isinstance(updated_question, dict) else getattr(updated_question, 'key_concept_id', None),
            is_custom=updated_question.get('is_custom', True) if isinstance(updated_question, dict) else getattr(updated_question, 'is_custom', True),
            created_at=updated_question.get('created_at') if isinstance(updated_question, dict) else updated_question.created_at,
            updated_at=updated_question.get('updated_at') if isinstance(updated_question, dict) else getattr(updated_question, 'updated_at', None),
            difficulty=updated_question.get('difficulty', 'medium') if isinstance(updated_question, dict) else getattr(updated_question, 'difficulty', 'medium'),
            explanation=updated_question.get('explanation', '') if isinstance(updated_question, dict) else getattr(updated_question, 'explanation', '')
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
        
        # Convert to response model using the correct dictionary keys from the repository
        response_data = KeyConceptResponse(
            id=new_concept['id'],
            file_id=new_concept['file_id'],
            concept_title=new_concept['concept_title'],
            concept=new_concept['concept_title'],  # Alias for frontend compatibility
            concept_explanation=new_concept['concept_explanation'],
            source_page_number=new_concept.get('source_page_number'),
            source_video_timestamp_start_seconds=new_concept.get('source_video_timestamp_start_seconds'),
            source_video_timestamp_end_seconds=new_concept.get('source_video_timestamp_end_seconds'),
            is_custom=new_concept.get('is_custom', False),
            created_at=new_concept.get('created_at'),
            updated_at=new_concept.get('updated_at')
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
    response_model=dict,
    tags=["Key Concepts"]
)
async def get_key_concepts_for_file(
    file_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> dict:
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
        
        # Convert to frontend-compatible format
        formatted_concepts = []
        for concept in concepts:
            formatted_concept = {
                "id": concept.id,
                "file_id": concept.file_id,
                "concept_title": concept.concept_title,
                "concept": concept.concept_title,  # Alias for frontend compatibility
                "concept_explanation": concept.concept_explanation,
                "source_page_number": concept.source_page_number,
                "source_video_timestamp_start_seconds": concept.source_video_timestamp_start_seconds,
                "source_video_timestamp_end_seconds": concept.source_video_timestamp_end_seconds,
                "is_custom": concept.is_custom,
                "display_order": None,  # Frontend expects this field
                "created_at": concept.created_at.isoformat() if concept.created_at else None,
                "updated_at": concept.updated_at.isoformat() if concept.updated_at else None
            }
            formatted_concepts.append(formatted_concept)
        
        # Return response in frontend-expected format
        logger.info(f"[API] Successfully retrieved {len(formatted_concepts)} key concepts for file {file_id}")
        return {
            "status": "success",
            "data": {
                "key_concepts": formatted_concepts
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting key concepts: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving key concepts"
        )

@files_router.put("/{file_id}/key-concepts/{key_concept_id}", response_model=dict, status_code=status.HTTP_200_OK)
async def update_key_concept(
    file_id: int,
    key_concept_id: int, 
    key_concept_update_data: KeyConceptUpdate, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> dict:
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
        store.learning_material_repo.update_key_concept(
            concept_id=key_concept_id,
            update_data=key_concept_update_data
        )
        
        # Get the updated concept - repository returns a dictionary
        updated_concept = store.learning_material_repo.update_key_concept(
            concept_id=key_concept_id,
            update_data=key_concept_update_data
        )
        
        if not updated_concept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key concept not found or update failed"
            )
        
        # Use the dictionary returned by the repository
        response_data = {
            "id": updated_concept.get('id'),
            "file_id": updated_concept.get('file_id'),
            "concept_title": updated_concept.get('concept_title'),
            "concept": updated_concept.get('concept_title'),  # Alias for frontend compatibility
            "concept_explanation": updated_concept.get('concept_explanation'),
            "source_page_number": updated_concept.get('source_page_number'),
            "source_video_timestamp_start_seconds": updated_concept.get('source_video_timestamp_start_seconds'),
            "source_video_timestamp_end_seconds": updated_concept.get('source_video_timestamp_end_seconds'),
            "is_custom": updated_concept.get('is_custom'),
            "display_order": None,  # Frontend expects this field
            "created_at": updated_concept.get('created_at').isoformat() if updated_concept.get('created_at') else None,
            "updated_at": updated_concept.get('updated_at').isoformat() if updated_concept.get('updated_at') else None
        }
        
        return {
            "status": "success",
            "data": response_data,
            "message": "Key concept updated successfully."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating key concept {key_concept_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the key concept."
        )


@files_router.delete("/{file_id}/key-concepts/{key_concept_id}", response_model=dict)
async def delete_key_concept(
    file_id: int,
    key_concept_id: int, 
    user_data: Dict = Depends(authenticate_user), 
    store: RepositoryManager = Depends(get_store)
) -> dict:
    try:
        # Verify file ownership
        if not store.file_repo.check_user_file_ownership(file_id, user_data["user_id"]):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
            
        # Delete the key concept
        success = store.learning_material_repo.delete_key_concept(
            key_concept_id=key_concept_id, 
            user_id=user_data["user_id"]
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Key concept not found or user does not have permission."
            )
            
        return {
            "status": "success",
            "message": "Key concept deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting key concept {key_concept_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the key concept"
        )