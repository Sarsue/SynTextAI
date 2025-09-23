from fastapi import APIRouter, Depends, HTTPException, Query, Path, Request, Header
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Any
import logging
import os
from datetime import datetime

from ..repositories import RepositoryManager, get_repository_manager
from ..models.orm_models import ChatHistory, Message
from ..models.chat import ChatHistory, ChatHistoryCreate, ChatHistoryUpdate
from ..dependencies import authenticate_user

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
histories_router = APIRouter(prefix="/api/v1/histories", tags=["histories"])

# Get repository manager dependency
async def get_repo_manager() -> RepositoryManager:
    return await get_repository_manager()

# Route to create a new chat history
@histories_router.post("", response_model=ChatHistory, status_code=201)
async def create_history(
    title: str = Query(..., description="Title of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    try:
        user_id = user_data["user_id"]
        history = await repo_manager.chat_repo.create_chat_history(
            title=title,
            user_id=user_id
        )
        return history
    except Exception as e:
        logger.error(f"Error creating chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Route to get all chat histories for a user
@histories_router.get("", response_model=List[Dict[str, Any]])  # Using Dict for flexibility with message format
async def get_history_messages(
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    try:
        user_id = user_data["user_id"]
        chat_histories = await repo_manager.chat_repo.get_user_chats(user_id=user_id)
        return chat_histories
    except Exception as e:
        logger.error(f"Error retrieving chat histories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Route to get messages for a specific chat history
@histories_router.get("/{history_id}", response_model=ChatHistory)
async def get_specific_history_messages(
    history_id: int = Path(..., description="ID of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    try:
        user_id = user_data["user_id"]
        chat = await repo_manager.chat_repo.get_chat_with_messages(
            chat_id=history_id,
            include_messages=True
        )
        if not chat or chat.user_id != user_id:
            raise HTTPException(status_code=404, detail="Chat history not found")
        return chat
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving messages for history {history_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Route to delete a specific chat history
@histories_router.delete("/{history_id}", status_code=200)
async def delete_specific_history_messages(
    history_id: int = Path(..., description="ID of the chat history to delete"),
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    try:
        user_id = user_data["user_id"]
        # First verify the chat belongs to the user
        chat = await repo_manager.chat_repo.get_chat_with_messages(history_id)
        if not chat or chat.user_id != user_id:
            raise HTTPException(status_code=404, detail="Chat history not found")
            
        # Delete the chat
        success = await repo_manager.chat_repo.delete_chat(history_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete chat history")
            
        return {"message": "History deleted successfully", "deletedHistoryId": history_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting history {history_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Route to delete all chat histories for a user
@histories_router.delete("/all", status_code=200)
async def delete_all_user_histories(
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    try:
        user_id = user_data["user_id"]
        # Get all user's chats
        chats = await repo_manager.chat_repo.get_user_chats(user_id=user_id)
        
        # Delete each chat
        for chat in chats:
            await repo_manager.chat_repo.delete_chat(chat.id)
            
        return {"message": "All histories deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting all histories for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))