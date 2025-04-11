import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks, Query, Body
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional, Union, Any
from docsynth_store import DocSynthStore
from pydantic import BaseModel, Field
from llm_service import prompt_llm

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request, store: DocSynthStore = Depends(get_store)):
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
    files: List[UploadFile] = File(..., description="List of files to upload"),
    user_data: Dict = Depends(authenticate_user)
):
    try:
        from tasks import process_file_data  # Ensure this import is correct

        user_id = user_data["user_id"]
        user_gc_id = user_data["user_gc_id"]
        store = request.app.state.store

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

            store.add_file(user_id, file.filename, file_url)
            background_tasks.add_task(process_file_data, user_id, user_gc_id, file.filename, language)
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

# Route to re-extract a file
@files_router.patch("/{file_id}/reextract", status_code=status.HTTP_202_ACCEPTED)
async def reextract_file(
    file_id: int,
    request: Request,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store

        file_info = store.get_file_entry(user_id, file_id)
        if not file_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        # Placeholder for re-extraction logic
        # process_file(file_info['file_path'])  # Replace with your re-processing logic

        return {"message": "File re-extraction initiated"}
    except Exception as e:
        logger.error(f"Error reextracting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
        
# Model for explain content request
class ExplainRequest(BaseModel):
    fileId: int
    fileType: str  # 'pdf' or 'video'
    selectionType: str  # 'text' or 'video_range'
    content: Optional[str] = None  # Selected text content for PDF
    page: Optional[int] = 1  # Page number for PDF
    videoStart: Optional[float] = None  # Start timestamp for video (seconds)
    videoEnd: Optional[float] = None  # End timestamp for video (seconds)

# Route to explain file content
@files_router.post("/explain", status_code=status.HTTP_200_OK)
async def explain_file_content(
    request: Request,
    data: ExplainRequest = Body(...),
    user_data: Dict = Depends(authenticate_user)
):
    """Generate AI-powered explanations for selected content in files.
    
    Premium users get more detailed explanations with longer context windows.
    Free users have limited monthly usage.
    """
    try:
        user_id = user_data["user_id"]
        store = request.app.state.store
        
        # Check if file exists and belongs to the user
        file = store.get_file_by_id(data.fileId)
        if not file or file.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        
        # Check user's subscription status
        #is_premium = store.is_premium_user(user_id)
        
        # Generate the explanation based on file type and selection
        explanation = ""
        
        if data.selectionType == "text":
            # Format prompt for text explanation (PDF)
            if not data.content or not data.content.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No text content provided to explain")
                
            explanation = await generate_text_explanation(
                content=data.content,
                file_name=file.file_name,
                page=data.page,
                is_premium=is_premium
            )
            
        elif data.selectionType == "video_range":
            # Format prompt for video time range explanation
            if data.videoStart is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No video timestamp provided")
                
            explanation = await generate_video_explanation(
                file_name=file.file_name,
                start_time=data.videoStart,
                end_time=data.videoEnd or data.videoStart,
                is_premium=is_premium
            )
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid selection type")
        
        # Save the explanation to the database
        explanation_id = store.save_explanation(
            user_id=user_id,
            file_id=data.fileId,
            selection_type=data.selectionType,
            content=data.content,
            explanation=explanation,
            page=data.page,
            video_start=data.videoStart,
            video_end=data.videoEnd
        )
        
        return {
            "explanation": explanation,
            "explanationId": explanation_id,
            "isPremium": is_premium
        }
        
    except Exception as e:
        logger.error(f"Error generating explanation: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
        
async def generate_text_explanation(content: str, file_name: str, page: int = 1, is_premium: bool = False) -> str:
    """Generate an explanation for text selection from a document"""
    try:
        # Prepare the prompt based on subscription tier
        if is_premium:
            system_prompt = (
                "You are an expert document analyst providing detailed explanations of text selections. "
                "Your explanations should be thorough, clear, and insightful, helping the user deeply "
                "understand the selected content."
            )
        else:
            system_prompt = (
                "You are a helpful assistant providing brief explanations of text selections. "
                "Keep your explanations concise and to the point."
            )
        
        # Create the user prompt with the selected text
        user_prompt = f"Please explain the following text selection from the document '{file_name}' (page {page}):\n\n{content}"
        
        # Combine prompts
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        # Call the existing prompt_llm function from llm_service
        explanation = prompt_llm(full_prompt)
        
        return explanation
        
    except Exception as e:
        logger.error(f"Error generating text explanation: {e}")
        return "An error occurred while generating the explanation. Please try again."

async def generate_video_explanation(file_name: str, start_time: float, end_time: float = None, is_premium: bool = False) -> str:
    """Generate an explanation for a video clip"""
    try:
        # Format the time range string
        if end_time and end_time > start_time:
            time_range = f"from {start_time:.1f}s to {end_time:.1f}s"
        else:
            time_range = f"at {start_time:.1f}s"
        
        # Create prompt based on subscription tier
        if is_premium:
            system_prompt = (
                "You are an expert video content analyst providing detailed explanations of video segments. "
                "Your explanations should help the user understand what is happening in the selected "
                "portion of the video with thorough, clear, and insightful commentary."
            )
        else:
            system_prompt = (
                "You are a helpful assistant providing brief explanations of video segments. "
                "Keep your explanations of what might be happening in the video concise and to the point."
            )
            
        # Create the user prompt for the video segment
        user_prompt = f"Please explain what might be happening in this segment of the video '{file_name}' {time_range}."
        
        # Combine prompts
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        # Call the existing prompt_llm function from llm_service
        explanation = prompt_llm(full_prompt)
        
        return explanation
            
    except Exception as e:
        logger.error(f"Error generating video explanation: {e}")
        return "An error occurred while generating the explanation. Please try again."