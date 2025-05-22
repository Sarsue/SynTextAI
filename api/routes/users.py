from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from typing import Dict
from utils import decode_firebase_token
from docsynth_store import DocSynthStore
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(authorization: str = Header(None), store: DocSynthStore = Depends(get_store)):
    if not authorization or not authorization.startswith("Bearer "):
        logger.error("Invalid or missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    if not success:
        logger.error("Failed to authenticate user with token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = store.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Route to create a new user
@users_router.post("", status_code=201)
async def create_user(
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_info = user_data["user_info"]
        name = user_info['name']
        email = user_info['email']
        user = store.add_user(email, name)
        logger.info(f"Created user: {user}")
        return user
    except IntegrityError:
        logger.error(f"Database error while creating user {user_info['email']}")
        raise HTTPException(status_code=400, detail="User already exists")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

# Route to delete a user
@users_router.delete("", status_code=200)
async def delete_user(
    background_tasks: BackgroundTasks,
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]
        user_gc_id = user_info['user_id']

        # Trigger Celery task to delete user and associated files
        background_tasks.add_task(delete_user_task, user_id, user_gc_id)
        return {"message": "User deletion in progress", "email": user_info['email']}
    except IntegrityError:
        logger.error(f"Database error while deleting user {user_info['email']}")
        raise HTTPException(status_code=500, detail="Failed to delete user due to database constraints")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")