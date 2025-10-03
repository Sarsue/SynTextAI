import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile, Request, Response, status, Query, BackgroundTasks
from typing import List, Dict, Optional, TypeVar, Any
from redis.exceptions import RedisError
from ..utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from ..repositories import RepositoryManager
from ..models.orm_models import File
from fastapi.responses import JSONResponse
from ..middleware.auth import get_current_user
from ..services.agent_service import agent_service
from ..models.learning_content import (
    StandardResponse,
    KeyConceptCreate, KeyConceptResponse, KeyConceptUpdate, FlashcardCreate,
    FlashcardResponse, FlashcardsListResponse, FlashcardUpdateRequest, QuizQuestionCreate,
    QuizQuestionResponse, QuizQuestionsListResponse, QuizQuestionUpdate
)
from sqlalchemy import func
from ..models.file_schemas import (
    PaginatedResponse,
    FileResponse,
    FileUploadResponse,
    YouTubeURLRequest,
    UploadResponse
)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Define a standardized API response model
T = TypeVar('T')

# Dependency to get the repository manager
async def get_repository_manager() -> RepositoryManager:
    """Get an initialized repository manager."""
    from ..dependencies import get_repository_manager as get_repo_manager
    return await get_repo_manager()

# Get current user from request state
async def get_current_user(request: Request) -> Dict[str, Any]:
    """Get the current authenticated user from request state."""
    if not hasattr(request.state, 'user'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return request.state.user

async def process_file_with_ingestion_agent(
    file_id: int,
    file_path: str,
    file_type: str,
    language: str,
    comprehension_level: str,
    user_id: str,
    repo_manager: RepositoryManager,
    file_data: Optional[bytes] = None,
    filename: Optional[str] = None,
    is_youtube: bool = False
):
    """
    Process a file or YouTube URL using the IngestionAgent.
    
    This is the unified entry point for all file processing, handling both:
    - File uploads (PDF, text, etc.)
    - YouTube URLs
    
    Args:
        file_id: ID of the file in the database
        file_path: Path to the file or YouTube URL
        file_type: Type of file (pdf, youtube, txt, etc.)
        language: Language code for processing
        comprehension_level: User's comprehension level
        user_id: ID of the user who uploaded the file
        repo_manager: Repository manager for database operations
        file_data: Raw file data (for direct processing)
        filename: Original filename (for uploads)
        is_youtube: Whether this is a YouTube URL
    
    Returns:
        Dict containing processing results
    """
    from api.websocket_manager import websocket_manager as websocket_service
    
    async def update_status(status: str, error: Optional[str] = None, progress: float = 0.0):
        """Update the processing status and notify via WebSocket."""
        try:
            # Use the file repository to update the file status
            await repo_manager.file_repo.update_file_status(
                file_id=file_id,
                status=status,
                error_message=error
            )
            
            try:
                # Notify via WebSocket
                await websocket_manager.send_message(
                    user_id,
                    "file_status_update",
                    {
                        "file_id": file_id,
                        "status": status,
                        "error_message": error,
                        "progress": progress
                    }
                )
            except Exception as ws_error:
                logger.debug("WebSocket service not available, skipping status update")
            
            return True
        except Exception as e:
            logger.error(f"Error updating status: {e}", exc_info=True)
            return False
    
    try:
        # Prepare metadata for the ingestion agent
        metadata = {
            "file_id": file_id,
            "user_id": user_id,
            "language": language,
            "comprehension_level": comprehension_level,
            "is_youtube": is_youtube
        }
        
        # Update status to processing
        await update_status("processing", progress=0.1)
        
        # Determine the source type
        source_type = "youtube" if is_youtube else file_type
        
        # Prepare content for the agent
        content = {
            "source_type": source_type,
            "metadata": metadata,
            "file_type": file_type,
        }
        
        # Add file content or URL based on the source
        if is_youtube:
            content["youtube_url"] = file_path
            content["url"] = file_path  # Add url field for compatibility with _process_youtube_new
        else:
            # For file uploads, we need to handle the file data
            if file_data:
                # If file data is provided directly, use it
                content["file_data"] = file_data
                content["file_path"] = "in_memory_file"  # Add file_path for compatibility
            else:
                # Otherwise, use the file path
                content["file_path"] = file_path
                
            if filename:
                content["filename"] = filename
        
        # Use the IngestionAgent to process the file
        logger.info(f"Processing {'YouTube URL' if is_youtube else 'file'} with IngestionAgent")
        logger.debug(f"Content being sent to agent: {content}")
        
        # Pass the content as kwargs to ensure all fields are properly passed through
        result = await agent_service.process_content(
            agent_name="ingestion",
            content=content,  # This will be the 'content' field in input_data
            content_type="json",
            **content  # This ensures all content fields are available at the top level of input_data
        )
        
        if not result:
            raise ValueError("No result returned from IngestionAgent")
        
        # Update status to processed
        await update_status("processed", progress=1.0)
        
        # Store the processing results (key concepts, etc.)
        if result.get("key_concepts"):
            for concept in result["key_concepts"]:
                # Handle both YouTube and PDF key concept formats
                key_concept_data = {
                    "concept_title": concept.get("concept_title") or concept.get("title", ""),
                    "concept_explanation": concept.get("concept_explanation") or concept.get("explanation", ""),
                    "source_page_number": concept.get("source_page_number") or concept.get("page_number"),
                    "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds") or concept.get("timestamp_start"),
                    "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds") or concept.get("timestamp_end"),
                    "is_custom": concept.get("is_custom", False)
                }
                
                # Add source type and reference if available
                if "source_type" in concept:
                    key_concept_data["source_type"] = concept["source_type"]
                if "source_reference" in concept:
                    key_concept_data["source_reference"] = concept["source_reference"]
                
                # Add any metadata
                if "metadata" in concept and isinstance(concept["metadata"], dict):
                    key_concept_data["metadata"] = concept["metadata"]
                
                await repo_manager.learning_material_repo.add_key_concept(file_id, key_concept_data)
        
        # Update file with any additional metadata from processing
        update_data = {
            "status": "processed"
        }
        if "title" in result:
            update_data["title"] = result["title"]
        if "summary" in result:
            update_data["summary"] = result["summary"]
            
        # Update the file status using the file repository
        await repo_manager.file_repo.update_file_status(
            file_id=file_id,
            **update_data
        )
        
        return result
        
    except Exception as e:
        error_msg = f"Error processing file with IngestionAgent: {str(e)}"
        logger.error(error_msg, exc_info=True)
        await update_status("failed", error=error_msg)
        raise

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=UploadResponse)
async def save_file(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = FastAPIFile(None, description="List of files to upload"),
    _user: Dict = Depends(get_current_user),
    youtube_data: Optional[YouTubeURLRequest] = None,
    language: str = Query("en", description="Language code (e.g., 'en', 'es')"),
    comprehension_level: str = Query("beginner", description="Comprehension level (beginner, intermediate, advanced)")
):
    """
    Handle file uploads and process them using the IngestionAgent.
    
    This is the single entry point for all file processing, including:
    - YouTube URLs (via JSON or form data)
    - File uploads (PDF, text, etc.)
    
    All processing is delegated to the IngestionAgent with appropriate metadata.
    """
    try:
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        repo_manager = user_data["store"]
        
        content_type = request.headers.get("content-type", "")
        is_json = "application/json" in content_type
        is_multipart = "multipart/form-data" in content_type
        
        # Get Firebase token from Authorization header if available
        auth_header = request.headers.get('Authorization')
        firebase_token = None
        if auth_header and auth_header.startswith('Bearer '):
            firebase_token = auth_header.split(' ')[1]
        
        # Handle YouTube URL (can come from JSON or form data)
        if youtube_data or (is_json and not is_multipart):
            try:
                # Get the YouTube URL from either the JSON body or form data
                if youtube_data:
                    url = youtube_data.url.strip()
                    language = youtube_data.language or language
                    comprehension_level = youtube_data.comprehension_level or comprehension_level
                else:
                    # Try to parse JSON body
                    try:
                        body = await request.json()
                        url = body.get('url', '').strip()
                        language = body.get('language', language)
                        comprehension_level = body.get('comprehension_level', comprehension_level)
                    except Exception as e:
                        logger.error(f"Error parsing JSON body: {e}")
                        raise HTTPException(status_code=400, detail="Invalid JSON body")
                
                # Validate YouTube URL format
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
                
                # Create file record for YouTube
                file_data = {
                    'user_id': user_id,
                    'file_name': url,
                    'file_url': url,
                    'file_type': 'youtube'
                }
                file = await repo_manager.file_repo.create_file(file_data, return_id=True)
                file_id = file if file is not None else None
                
                if not file_id:
                    raise HTTPException(status_code=500, detail="Failed to create file record for YouTube URL.")
                
                # Process in background using IngestionAgent
                background_tasks.add_task(
                    process_file_with_ingestion_agent,
                    file_id=file_id,
                    file_path=url,  # Pass URL as file_path for YouTube
                    file_type="youtube",
                    language=language,
                    comprehension_level=comprehension_level,
                    user_id=user_id,
                    repo_manager=repo_manager,
                    is_youtube=True  # Explicitly set is_youtube flag
                )
                
                return UploadResponse(
                    message="YouTube URL processing started",
                    file_id=file_id,
                    file_name=url,
                    files=[{
                        "id": file_id,
                        "file_name": url,
                        "file_url": url,
                        "status": "processing"
                    }]
                )
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error processing YouTube URL: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
            
        # Handle file upload
        elif content_type.startswith("multipart/form-data"):
            if not files:
                raise HTTPException(status_code=400, detail="No files were uploaded.")
            
            uploaded_files_responses = []
            for file in files:
                try:
                    # Validate file before upload
                    try:
                        # Check file size (10MB max)
                        max_size = 10 * 1024 * 1024  # 10MB
                        file.file.seek(0, 2)  # Seek to end
                        file_size = file.file.tell()
                        file.file.seek(0)  # Reset file pointer
                        
                        if file_size > max_size:
                            raise HTTPException(
                                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail=f"File {file.filename} is too large. Maximum size is 10MB."
                            )
                        file.file.seek(0)  # Reset file pointer
                        
                        # Check file type
                        file_extension = os.path.splitext(file.filename)[1].lower()
                        allowed_extensions = [".pdf", ".txt", ".docx", ".doc", ".md", ".html", ".htm"]
                        if file_extension not in allowed_extensions:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"File type {file_extension} is not supported. Supported types: {', '.join(allowed_extensions)}"
                            )
                            
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.error(f"Error validating file {file.filename}: {e}", exc_info=True)
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid file: {str(e)}"
                        )
                    
                    # Generate a unique filename
                    file_extension = os.path.splitext(file.filename)[1].lower()
                    unique_filename = f"{user_id}_{int(time.time())}_{secrets.token_hex(4)}{file_extension}"
                    
                    # Upload to GCS
                    try:
                        file_url = await upload_to_gcs(
                            file.file,
                            unique_filename,
                            user_gc_id,
                            content_type=file.content_type
                        )
                    except Exception as e:
                        logger.error(f"Error uploading to GCS: {e}", exc_info=True)
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to upload file to storage"
                        )
                    
                    # Create file record in database
                    try:
                        file_type = file_extension[1:]  # Remove the dot
                        
                        file_data = {
                            'user_id': user_id,
                            'file_name': file.filename,
                            'file_url': file_url,
                            'file_type': file_type
                        }
                        file = await repo_manager.file_repo.create_file(file_data, return_id=True)
                        file_id = file if file is not None else None
                        
                        if not file_id:
                            raise HTTPException(
                                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Failed to create file record in database"
                            )
                        
                        # Read file data for processing
                        file.file.seek(0)
                        file_data = file.file.read()
                        
                        # Process in background using IngestionAgent
                        background_tasks.add_task(
                            process_file_with_ingestion_agent,
                            file_id=file_id,
                            file_path=file_url,  # Use URL for reference
                            file_type=file_type,
                            language=language,
                            comprehension_level=comprehension_level,
                            user_id=user_id,
                            repo_manager=repo_manager,
                            file_data=file_data,  # Pass file data directly
                            filename=file.filename
                        )
                        
                        uploaded_files_responses.append({
                            "id": file_id,
                            "file_name": file.filename,
                            "file_url": file_url,
                            "status": "processing"
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing file record: {e}", exc_info=True)
                        # Attempt to clean up the uploaded file if database operation failed
                        try:
                            delete_from_gcs(file_url, user_gc_id)
                        except Exception as cleanup_error:
                            logger.error(f"Failed to clean up file after database error: {cleanup_error}")
                        
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to process file: {str(e)}"
                        )
                        
                except HTTPException as http_error:
                    # Re-raise HTTP exceptions to be handled by FastAPI
                    raise http_error
                    
                except Exception as e:
                    logger.error(f"Error processing file {file.filename}: {e}", exc_info=True)
                    continue
                
            response = UploadResponse(
                message=f"Successfully uploaded {len(uploaded_files_responses)} files",
                files=uploaded_files_responses
            )
            
            # For backward compatibility, set the first file's info at the top level
            if uploaded_files_responses:
                response.file_id = uploaded_files_responses[0]['id']
                response.file_name = uploaded_files_responses[0]['file_name']
                
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
@files_router.get("", response_model=PaginatedResponse[FileResponse])
async def get_files(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    _user: Dict = Depends(get_current_user)
):
    """
    Retrieve paginated list of files for the authenticated user.
    
    Args:
        page: Page number (1-based)
        page_size: Number of items per page (1-100)
        user_data: Authenticated user data
        
    Returns:
        Paginated list of files with metadata
    """
    try:
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Get files from repository with pagination
            files = await repo_manager.file_repo.get_user_files(
                user_id=user_id,
                page=page,
                page_size=page_size,
                session=session
            )
            
            # Get total count for pagination
            total = await repo_manager.file_repo.count_user_files(
                user_id=user_id,
                session=session
            )
            
            # Format response
            return {
                "items": [
                    {
                        "id": file.id,
                        "file_name": file.file_name,
                        "file_url": file.file_url,
                        "file_type": file.file_type,
                        "status": file.status,
                        "created_at": file.created_at.isoformat(),
                        "updated_at": file.updated_at.isoformat()
                    } for file in files
                ],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving files: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to retrieve files. Please try again later."
        )

# Route to delete a file
@files_router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: int,
    request: Request,
    _user: Dict = Depends(get_current_user)
):
    """
    Delete a file and its associated data.
    
    Args:
        file_id: ID of the file to delete
        user_data: Authenticated user data
        
    Returns:
        HTTP 204 No Content on success
    """
    try:
        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Get file repository
            file_repo = await repo_manager.file_repo
            
            # Get the file to delete
            file_to_delete = await file_repo.get_file_by_id(
                file_id=file_id,
                session=session
            )
            
            # Verify file exists and belongs to user
            if not file_to_delete or file_to_delete.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, 
                    detail="File not found or unauthorized."
                )
            
            # Delete from GCS if applicable
            if file_to_delete.file_url and "storage.googleapis.com" in file_to_delete.file_url:
                try:
                    await delete_from_gcs(file_to_delete.file_url, user_gc_id)
                except Exception as e:
                    logger.error(f"Error deleting file from GCS: {e}", exc_info=True)
                    # Continue with database deletion even if GCS deletion fails
            
            # Delete the file record
            success = await file_repo.delete(
                id=file_id,
                session=session
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to delete file entry from database."
                )
            
            # Commit the transaction
            await session.commit()
            
        return Response(status_code=status.HTTP_204_NO_CONTENT)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the file"
        )

# --- Quiz Questions ---

@files_router.get(
    "/{file_id}/quiz-questions",
    response_model=QuizQuestionsListResponse
)
async def get_quiz_questions_for_file(
    request: Request,
    file_id: int,
    _user: Dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page")
) -> dict:
    """
    Get all quiz questions for a specific file.
    
    Args:
        file_id: The ID of the file to get quiz questions for
        page: Page number for pagination (default: 1)
        page_size: Number of items per page (default: 10, max: 100)
        user_data: Authenticated user data
        
    Returns:
        Dictionary containing quiz questions with pagination info
    """
    try:
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Verify file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Fetch quiz questions with pagination
            quiz_questions = await learning_material_repo.get_quiz_questions_for_file(
                file_id=file_id,
                page=page,
                page_size=page_size,
                session=session
            )
            
            # Get total count for pagination
            total_count = await learning_material_repo.count_quiz_questions_for_file(
                file_id=file_id,
                session=session
            )
            
            # Format response
            formatted_questions = []
            for question in quiz_questions:
                formatted_question = {
                    "id": question.get("id"),
                    "file_id": question.get("file_id"),
                    "question": question.get("question"),
                    "question_type": question.get("question_type"),
                    "options": question.get("options", []),
                    "distractors": question.get("distractors", []),
                    "correct_answer": question.get("correct_answer"),
                    "explanation": question.get("explanation", ""),
                    "key_concept_id": question.get("key_concept_id"),
                    "is_custom": question.get("is_custom", False),
                    "created_at": question.get("created_at").isoformat() if question.get("created_at") else None,
                    "updated_at": question.get("updated_at").isoformat() if question.get("updated_at") else None
                }
                formatted_questions.append(formatted_question)
            
            # Return response with pagination info
            return {
                "status": "success",
                "data": {
                    "quiz_questions": formatted_questions,
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
                }
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving quiz questions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving quiz questions"
        )

# --- Learning Content Endpoints---

# Helper for ownership check
async def check_ownership(file_id: int, user_data: Dict):
    """
    Helper function to check if the user owns the file.
    
    Args:
        file_id: ID of the file to check ownership for
        user_data: Authenticated user data
        
    Raises:
        HTTPException: If the user doesn't own the file or it doesn't exist
    """
    user_id = user_data["user_id"]
    
    # Get repository manager
    repo_manager = await get_repository_manager()
    
    async with repo_manager.session_scope() as session:
        # Get file repository
        file_repo = await repo_manager.file_repo
        
        # Check file ownership
        is_owner = await file_repo.check_user_file_ownership(
            file_id=file_id,
            user_id=user_id,
            session=session
        )
        
        if not is_owner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found or you don't have permission to access it."
            )
    return True

# --- Flashcards ---

@files_router.get("/{file_id}/flashcards", response_model=dict)
async def get_flashcards_for_file(
    request: Request,
    file_id: int,
    _user: Dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page")
) -> dict:
    """
    Get all flashcards for a specific file.
    
    Args:
        file_id: The ID of the file to get flashcards for
        page: Page number for pagination (default: 1)
        page_size: Number of items per page (default: 10, max: 100)
        user_data: Authenticated user data
        
    Returns:
        Dictionary containing flashcards with pagination info
    """
    try:
        logger.info(f"[API] Getting flashcards for file {file_id} (page {page}, size {page_size})")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get file details for context
            file = await file_repo.get_file_by_id(
                file_id=file_id,
                session=session
            )
            if not file:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Get existing flashcards from the database with pagination
            db_flashcards = await learning_material_repo.get_flashcards_for_file(
                file_id=file_id,
                page=page,
                page_size=page_size,
                session=session
            )
            
            # Get total count for pagination
            total_count = await learning_material_repo.count_flashcards_for_file(
                file_id=file_id,
                session=session
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
                        "page_size": page_size,
                        "total_pages": 0
                    }
                }
            
            # Format the response with frontend-compatible structure
            formatted_flashcards = []
            for card in db_flashcards:
                formatted_card = {
                    "id": card.get("id"),
                    "file_id": card.get("file_id"),
                    "question": card.get("question"),
                    "answer": card.get("answer"),
                    "key_concept_id": card.get("key_concept_id"),
                    "is_custom": card.get("is_custom", False),
                    "difficulty": card.get("difficulty", "medium"),
                    "created_at": card.get("created_at").isoformat() if card.get("created_at") else None,
                    "updated_at": card.get("updated_at").isoformat() if card.get("updated_at") else None
                }
                formatted_flashcards.append(formatted_card)
            
            logger.info(f"[API] Successfully retrieved {len(formatted_flashcards)} flashcards for file {file_id}")
            return {
                "status": "success",
                "data": {
                    "flashcards": formatted_flashcards,
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
                }
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving flashcards: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving flashcards"
        )

@files_router.post("/{file_id}/flashcards/generate", status_code=status.HTTP_200_OK, response_model=StandardResponse[FlashcardsListResponse])
async def generate_flashcards(
    request: Request,
    file_id: int,
    _user: Dict = Depends(get_current_user),
    count: int = Query(5, ge=1, le=20, description="Number of flashcards to generate"),
    difficulty: str = Query("medium", description="Difficulty level (easy, medium, hard)")
):
    """
    Generate flashcards for a file using the QuizAgent.
    
    Args:
        file_id: The ID of the file to generate flashcards for
        count: Number of flashcards to generate (1-20)
        difficulty: Difficulty level of flashcards (easy, medium, hard)
        user_data: Authenticated user data
        
    Returns:
        StandardResponse containing the generated flashcards
    """
    try:
        logger.info(f"[API] Generating {count} flashcards for file {file_id}")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get file details
            file = await file_repo.get_file_by_id(
                file_id=file_id,
                session=session
            )
            if not file:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Get key concepts for context
            key_concepts_data = await learning_material_repo.get_key_concepts_for_file(
                file_id=file_id,
                session=session
            )
            
            key_concepts = [
                {"id": kc.get("id"), "title": kc.get("concept_title"), "explanation": kc.get("concept_explanation")}
                for kc in key_concepts_data
                if kc.get("concept_title")  # Only include key concepts with a title
            ]
            
            # Get file content
            file_content = await file_repo.get_file_content(
                file_id=file_id,
                session=session
            )
            
            if not file_content:
                file_content = ''
            
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
                flashcard = await learning_material_repo.add_flashcard(
                    file_id=file_id,
                    user_id=user_id,
                    question=card.question if hasattr(card, 'question') else card.get('question', ''),
                    answer=card.answer if hasattr(card, 'answer') else card.get('answer', ''),
                    key_concept_id=card.key_concept_id if hasattr(card, 'key_concept_id') else card.get('key_concept_id'),
                    is_custom=False,
                    difficulty=card.difficulty if hasattr(card, 'difficulty') else card.get('difficulty', difficulty),
                    session=session
                )
                if flashcard:
                    saved_flashcards.append(flashcard)
            
            # Commit the transaction
            await session.commit()
            
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
            detail="An error occurred while generating flashcards"
        )

@files_router.post("/{file_id}/flashcards", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_flashcard_for_file(
    file_id: int, 
    flashcard_data: FlashcardCreate, 
    request: Request,
    _user: Dict = Depends(get_current_user)
) -> dict:
    """
    Add a new flashcard to a file.
    
    Args:
        file_id: The ID of the file to add the flashcard to
        flashcard_data: The flashcard data to add
        user_data: Authenticated user data
        
    Returns:
        Dictionary containing the created flashcard
    """
    try:
        logger.info(f"[API] Adding flashcard to file {file_id} by user {user_data['user_id']}")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Add the flashcard
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Add the flashcard
            flashcard = await learning_material_repo.add_flashcard(
                file_id=file_id,
                user_id=user_id,
                question=flashcard_data.question,
                answer=flashcard_data.answer,
                key_concept_id=flashcard_data.key_concept_id,
                is_custom=True,  # Manually added flashcards are always custom
                difficulty=flashcard_data.difficulty if hasattr(flashcard_data, 'difficulty') else 'medium',
                session=session
            )
            
            if not flashcard:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to add flashcard"
                )
            
            # Commit the transaction
            await session.commit()
            
            # Convert to response model
            flashcard_response = FlashcardResponse(
                id=flashcard.id,
                file_id=flashcard.file_id,
                question=flashcard.question,
                answer=flashcard.answer,
                key_concept_id=flashcard.key_concept_id if hasattr(flashcard, 'key_concept_id') else None,
                is_custom=getattr(flashcard, 'is_custom', True),
                created_at=flashcard.created_at,
                updated_at=getattr(flashcard, 'updated_at', None),
                difficulty=getattr(flashcard, 'difficulty', 'medium')
            )
            
            return {
                "status": "success",
                "data": {"flashcard": flashcard_response.dict()},
                "message": "Flashcard added successfully"
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding flashcard to file {file_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while adding the flashcard"
        )

@files_router.delete("/{file_id}/flashcards/{flashcard_id}", response_model=dict)
async def delete_flashcard(
    file_id: int,
    flashcard_id: int, 
    request: Request,
    _user: Dict = Depends(get_current_user)
) -> dict:
    """
    Delete a flashcard.
    
    Args:
        file_id: The ID of the file that the flashcard belongs to
        flashcard_id: The ID of the flashcard to delete
        user_data: Authenticated user data
        
    Returns:
        Dictionary with status and message
    """
    try:
        logger.info(f"[API] Deleting flashcard {flashcard_id} for file {file_id} by user {user_data['user_id']}")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Delete the flashcard
            success = await learning_material_repo.delete_flashcard(
                flashcard_id=flashcard_id,
                user_id=user_id,
                session=session
            )
            
            if not success:
                logger.error(f"[API] Failed to delete flashcard {flashcard_id} for file {file_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Flashcard not found or does not belong to the specified file"
                )
            
            # Commit the transaction
            await session.commit()
            
            logger.info(f"[API] Successfully deleted flashcard {flashcard_id} for file {file_id}")
            
            return {
                "status": "success",
                "message": "Flashcard deleted successfully"
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting flashcard {flashcard_id} for file {file_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the flashcard"
        )

@files_router.get(
    "/{file_id}/key-concepts",
    response_model=dict,
    tags=["Key Concepts"]
)
async def get_key_concepts_for_file(
    request: Request,
    file_id: int,
    _user: Dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page")
) -> dict:
    """
    Get all key concepts for a specific file.
    
    Args:
        file_id: The ID of the file to get key concepts for
        page: Page number for pagination (default: 1)
        page_size: Number of items per page (default: 10, max: 100)
        user_data: Authenticated user data
        
    Returns:
        Dictionary containing key concepts with pagination info
    """
    try:
        logger.info(f"[API] Getting key concepts for file {file_id} (page {page}, size {page_size})")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Get key concepts with pagination
            key_concepts = await learning_material_repo.get_key_concepts_for_file(
                file_id=file_id,
                page=page,
                page_size=page_size,
                session=session
            )
            
            # Get total count for pagination
            total_count = await learning_material_repo.count_key_concepts_for_file(
                file_id=file_id,
                session=session
            )
            
            # Format the response
            formatted_key_concepts = [
                {
                    "id": kc.get("id"),
                    "file_id": kc.get("file_id"),
                    "concept_title": kc.get("concept_title"),
                    "concept_explanation": kc.get("concept_explanation"),
                    "created_at": kc.get("created_at").isoformat() if kc.get("created_at") else None,
                    "updated_at": kc.get("updated_at").isoformat() if kc.get("updated_at") else None
                }
                for kc in key_concepts
            ]
            
            return {
                "status": "success",
                "data": {
                    "key_concepts": formatted_key_concepts,
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
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
    request: Request,
    _user: Dict = Depends(get_current_user)
) -> dict:
    """
    Update an existing key concept.
    
    Args:
        file_id: The ID of the file that the key concept belongs to
        key_concept_id: The ID of the key concept to update
        key_concept_update_data: The updated key concept data
        user_data: Authenticated user data
        
    Returns:
        Dictionary containing the updated key concept
    """
    try:
        logger.info(f"[API] Updating key concept {key_concept_id} for file {file_id}")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Update the key concept
            updated_concept = await learning_material_repo.update_key_concept(
                key_concept_id=key_concept_id,
                update_data=key_concept_update_data.dict(exclude_unset=True),
                user_id=user_id,
                session=session
            )
            
            if not updated_concept:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Key concept not found or update failed"
                )
            
            # Commit the transaction
            await session.commit()
            
            # Format the response
            response_data = {
                "id": updated_concept.id,
                "file_id": updated_concept.file_id,
                "concept_title": updated_concept.concept_title,
                "concept": updated_concept.concept_title,  # Alias for frontend compatibility
                "concept_explanation": updated_concept.concept_explanation,
                "source_page_number": getattr(updated_concept, 'source_page_number', None),
                "source_video_timestamp_start_seconds": getattr(updated_concept, 'source_video_timestamp_start_seconds', None),
                "source_video_timestamp_end_seconds": getattr(updated_concept, 'source_video_timestamp_end_seconds', None),
                "is_custom": getattr(updated_concept, 'is_custom', True),
                "display_order": None,  # Frontend expects this field
                "created_at": updated_concept.created_at.isoformat() if hasattr(updated_concept, 'created_at') and updated_concept.created_at else None,
                "updated_at": updated_concept.updated_at.isoformat() if hasattr(updated_concept, 'updated_at') and updated_concept.updated_at else None
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
    request: Request,
    _user: Dict = Depends(get_current_user)
) -> dict:
    """
    Delete a key concept.
    
    Args:
        file_id: The ID of the file that the key concept belongs to
        key_concept_id: The ID of the key concept to delete
        user_data: Authenticated user data
        
    Returns:
        Dictionary with status and message
    """
    try:
        logger.info(f"[API] Deleting key concept {key_concept_id} from file {file_id}")
        user_id = user_data["user_id"]
        
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Check file ownership
            file_repo = await repo_manager.file_repo
            if not await file_repo.check_user_file_ownership(
                file_id=file_id,
                user_id=user_id,
                session=session
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found or you don't have permission to access it."
                )
            
            # Get learning material repository
            learning_material_repo = await repo_manager.learning_material_repo
            
            # Delete the key concept
            success = await learning_material_repo.delete_key_concept(
                key_concept_id=key_concept_id,
                user_id=user_id,
                session=session
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Key concept not found or you don't have permission to delete it"
                )
            
            # Commit the transaction
            await session.commit()
            
            logger.info(f"[API] Successfully deleted key concept {key_concept_id} from file {file_id}")
            
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