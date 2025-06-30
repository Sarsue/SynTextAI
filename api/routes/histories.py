from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from typing import Optional
from ..utils import get_user_id
from ..repositories.repository_manager import RepositoryManager
import logging
from typing import Dict
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI router
histories_router = APIRouter(prefix="/api/v1/histories", tags=["histories"])

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(authorization: str = Header(None), store: RepositoryManager = Depends(get_store)):
    if not authorization:
        logger.error("Missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    success, user_info = get_user_id(authorization)
    if not success:
        logger.error("Failed to authenticate user with token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = store.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Route to create a new chat history
@histories_router.post("", status_code=201)
async def create_history(
    title: str = Query(..., description="Title of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        history = store.add_chat_history(title, user_id)
        return history
    except Exception as e:
        logger.error(f"Error creating chat history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to get all chat histories for a user
@histories_router.get("")
async def get_history_messages(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        message_list = store.get_all_user_chat_histories(user_id)
        return message_list
    except Exception as e:
        logger.error(f"Error retrieving chat histories: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to get messages for a specific chat history
@histories_router.get("/messages")
async def get_specific_history_messages(
    history_id: int = Query(..., description="ID of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        message_list = store.get_messages_for_chat_history(user_id, history_id)
        return message_list
    except Exception as e:
        logger.error(f"Error retrieving messages for history {history_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to delete a specific chat history
@histories_router.delete("", status_code=200)
async def delete_specific_history_messages(
    history_id: int = Query(..., description="ID of the chat history to delete"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        store.delete_chat_history(user_id, history_id)
        return {"message": "History deleted successfully", "deletedHistoryId": history_id}
    except Exception as e:
        logger.error(f"Error deleting history {history_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to delete all chat histories for a user
@histories_router.delete("/all", status_code=200)
async def delete_all_user_histories(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        store.delete_all_user_histories(user_id)
        return {"message": "All histories deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting all histories for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))