import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs, delete_from_gcs
import logging
from celery.result import AsyncResult
from typing import Dict, Optional

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
files_router = APIRouter(prefix="/api/v1/files", tags=["files"])

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request):
    try:
        token = request.headers.get('Authorization')
        if not token:
            logger.error("Missing Authorization token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        success, user_info = get_user_id(token)
        if not success:
            logger.error("Failed to authenticate user with token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        user_id = request.app.state.store.get_user_id_from_email(user_info['email'])
        if not user_id:
            logger.error(f"No user ID found for email: {user_info['email']}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info(f"Authenticated user_id: {user_id}, user_gc_id: {user_info['user_id']}")
        return user_id, user_info['user_id']

    except Exception as e:
        logger.exception("Error during user authentication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to save file
@files_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def save_file(
     background_tasks: BackgroundTasks,
    request: Request,
    language: str = "English",
    comprehension_level: str = "dropout",
    files: list[UploadFile] = File(...),
    user_info: tuple = Depends(authenticate_user)
):
    try:
        user_id, user_gc_id = user_info
        store = request.app.state.store

        if not files:
            logger.warning('No files provided')
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided")

        for file in files:
            file_url = upload_to_gcs(file.file, user_gc_id, file.filename)
            store.add_file(user_id, file.filename, file_url)
            if not file_url:
                logger.error(f"Failed to upload {file.filename} to GCS")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File upload failed")

            background_tasks.add_task(process_file_data, user_id, user_gc_id, filename, language)
            logger.info(f"Enqueued Task for processing {file.filename}")

        return {"message": "File processing queued."}

    except RedisError as e:
        logger.error("Redis error")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to enqueue job")
    except Exception as e:
        logger.error(f"Exception occurred: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to retrieve files
@files_router.get("", response_model=list)
async def retrieve_files(request: Request, user_info: tuple = Depends(authenticate_user)):
    try:
        user_id, _ = user_info
        store = request.app.state.store
        files = store.get_files_for_user(user_id)
        return files
    except Exception as e:
        logger.error(f"Error retrieving files: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to delete a file
@files_router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: int, request: Request, user_info: tuple = Depends(authenticate_user)):
    try:
        user_id, user_gc_id = user_info
        store = request.app.state.store
        file_info = store.delete_file_entry(user_id, file_id)
        delete_from_gcs(user_gc_id, file_info['file_name'])
        return None
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to re-extract a file
@files_router.patch("/{file_id}/reextract", status_code=status.HTTP_202_ACCEPTED)
async def reextract_file(file_id: int, request: Request, user_info: tuple = Depends(authenticate_user)):
    try:
        user_id, _ = user_info
        store = request.app.state.store

        # Placeholder for re-extraction logic
        # file_info = store.get_file_entry(user_id, file_id)
        # if not file_info:
        #     raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        # process_file(file_info['file_path'])  # Replace with your re-processing logic

        return {"message": "File re-extraction initiated"}
    except Exception as e:
        logger.error(f"Error reextracting file: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# Route to check task result
@files_router.get("/result/{task_id}")
async def task_result(task_id: str) -> Dict[str, object]:
    result = AsyncResult(task_id)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": result.result if result.ready() else None,
    }