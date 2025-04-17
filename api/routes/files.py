import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks, Query, Body
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from typing import Dict, List, Optional, Union, Any
from docsynth_store import DocSynthStore, Explanation
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

# Pydantic models for Explanations
class ExplainRequest(BaseModel):
    context: str
    selection_type: str = Field(..., pattern="^(text|video_range)$") # 'text' or 'video_range'
    # Add other potential context details if needed, e.g., page number for PDF
    page: Optional[int] = None 
    video_start: Optional[float] = None
    video_end: Optional[float] = None

class ExplanationResponse(BaseModel):
    id: int
    file_id: int
    user_id: int
    context_info: Optional[str] = None # Combined context for frontend 
    explanation_text: Optional[str] = None
    created_at: datetime
    # Add specific fields if needed for frontend differentiation
    selection_type: str
    page: Optional[int] = None
    video_start: Optional[float] = None
    video_end: Optional[float] = None
    
    class Config:
        orm_mode = True

class ExplanationHistoryResponse(BaseModel):
    explanations: List[ExplanationResponse]

# Placeholder function definition (replace with actual implementation or import)
async def prompt_llm(prompt: str) -> str:
    # Replace this with your actual call to the LLM service
    logger.info(f"Simulating LLM call with prompt: {prompt[:100]}...")
    await asyncio.sleep(1) # Simulate processing time
    return f"This is an AI-generated explanation for: {prompt[:100]}..."

# Route to get explanation history for a file
@files_router.get("/{file_id}/explanations", response_model=ExplanationHistoryResponse, summary="Get Explanation History", description="Retrieves all explanations previously generated for a specific file by the user.")
async def get_explanation_history(
    file_id: int,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        # Check if file exists and belongs to user (optional, store method handles user_id)
        store = request.app.state.store
        file_record = store.get_file_by_id(file_id)
        if not file_record or file_record.user_id != user_data["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
            
        explanations_data = store.get_explanations_for_file(user_id=user_data["user_id"], file_id=file_id)
        
        # Map DB results to Pydantic models before returning
        response_list = []
        for exp_data in explanations_data:
            # Create the combined context_info for the frontend
            context_info = exp_data.get('content') # Default to content for text
            if exp_data.get('selection_type') == 'video_range':
                start = exp_data.get('video_start')
                end = exp_data.get('video_end')
                context_info = f"Video {start:.1f}s - {end:.1f}s" if start is not None and end is not None else "Video time range"
            elif exp_data.get('selection_type') == 'text' and exp_data.get('page') is not None:
                 context_info = f"Page {exp_data.get('page')}: {exp_data.get('content', '')[:50]}..." 
                 
            response_list.append(ExplanationResponse(
                id=exp_data['id'],
                file_id=file_id, # Or get from exp_data if available
                user_id=user_data["user_id"], # Or get from exp_data if available
                context_info=context_info, # Use the generated context_info
                explanation_text=exp_data.get('explanation'),
                created_at=exp_data['created_at'],
                selection_type=exp_data['selection_type'],
                page=exp_data.get('page'),
                video_start=exp_data.get('video_start'),
                video_end=exp_data.get('video_end')
            ))
            
        return ExplanationHistoryResponse(explanations=response_list)
    except Exception as e:
        logger.error(f"Error fetching explanation history for file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve explanation history")

# Route to generate and save a new explanation
@files_router.post("/{file_id}/explain", response_model=ExplanationResponse, status_code=status.HTTP_201_CREATED, summary="Generate and Save Explanation", description="Generates an AI explanation for a selected part of a file and saves it.")
async def explain_file_content(
    file_id: int,
    request: ExplainRequest,
    user_data: Dict = Depends(authenticate_user)
):
    try:
        # 1. Check if user is premium
        store = request.app.state.store
        if not store.is_premium_user(user_data["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Explain feature requires a premium subscription.")
            
        # 2. Check if file exists and belongs to the user
        file_record = store.get_file_by_id(file_id)
        if not file_record or file_record.user_id != user_data["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        # 3. Prepare the prompt for the LLM
        # TODO: Refine prompt engineering - maybe include file context?
        prompt = f"Explain the following content: {request.context}"
        
        # 4. Call the LLM service (replace placeholder)
        explanation_text = await prompt_llm(prompt)
        if not explanation_text:
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate explanation")

        # 5. Save the explanation to the database
        explanation_id = store.save_explanation(
            user_id=user_data["user_id"],
            file_id=file_id,
            selection_type=request.selection_type,
            content=request.context if request.selection_type == 'text' else None,
            explanation=explanation_text,
            page=request.page if request.selection_type == 'text' else None,
            video_start=request.video_start if request.selection_type == 'video_range' else None,
            video_end=request.video_end if request.selection_type == 'video_range' else None
        )
        
        # 6. Retrieve the saved explanation to return it
        # This is slightly inefficient but ensures we return the full object as stored
        # Alternatively, construct the response directly from inputs + ID + timestamp
        saved_explanation = store.get_explanation_by_id(explanation_id) # Assumes get_explanation_by_id exists
        if not saved_explanation:
             # Fallback: construct response manually if get_explanation_by_id doesn't exist or fails
             # Determine context_info based on type for the response
            context_info = request.context
            if request.selection_type == 'video_range':
                context_info = f"Video {request.video_start:.1f}s - {request.video_end:.1f}s" if request.video_start is not None and request.video_end is not None else "Video time range"
            elif request.selection_type == 'text' and request.page is not None:
                 context_info = f"Page {request.page}: {request.context[:50]}..." 
                 
            return ExplanationResponse(
                id=explanation_id,
                file_id=file_id,
                user_id=user_data["user_id"],
                context_info=context_info,
                explanation_text=explanation_text,
                created_at=datetime.utcnow(), # Approximate time
                selection_type=request.selection_type,
                page=request.page,
                video_start=request.video_start,
                video_end=request.video_end
            )
        
        # If get_explanation_by_id worked:
        context_info = saved_explanation.content
        if saved_explanation.selection_type == 'video_range':
            context_info = f"Video {saved_explanation.video_start:.1f}s - {saved_explanation.video_end:.1f}s" if saved_explanation.video_start is not None and saved_explanation.video_end is not None else "Video time range"
        elif saved_explanation.selection_type == 'text' and saved_explanation.page is not None:
            context_info = f"Page {saved_explanation.page}: {saved_explanation.content[:50]}..." 
            
        return ExplanationResponse(
            id=saved_explanation.id,
            file_id=saved_explanation.file_id,
            user_id=saved_explanation.user_id,
            context_info=context_info,
            explanation_text=saved_explanation.explanation,
            created_at=saved_explanation.created_at,
            selection_type=saved_explanation.selection_type,
            page=saved_explanation.page,
            video_start=saved_explanation.video_start,
            video_end=saved_explanation.video_end
        )

    except HTTPException as he:
        raise he # Re-raise HTTP exceptions directly
    except Exception as e:
        logger.error(f"Error explaining content for file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate or save explanation")

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