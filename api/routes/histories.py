from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..models.chat import ChatHistory, ChatHistoryCreate, ChatHistoryUpdate, Message
from ..repositories import AsyncChatRepository
from ..dependencies import get_repository_manager
from ..middleware.auth import get_current_user
from ..repositories.domain_models import UserInDB

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/api/v1/histories", tags=["histories"])

class ChatHistoryResponse(ChatHistory):
    """Response model for chat history with additional metadata."""
    message_count: int = Field(..., description="Number of messages in the chat history")
    created_at: datetime = Field(..., description="When the chat history was created")
    updated_at: datetime = Field(..., description="When the chat history was last updated")

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ChatHistoryResponse)
async def create_history(
    request: Request,
    title: str = Query(..., description="Title of the chat history"),
    _user: UserInDB = Depends(get_current_user)
) -> ChatHistoryResponse:
    """
    Create a new chat history for the authenticated user.
    
    Args:
        request: The incoming HTTP request
        title: Title for the new chat history
        user: The authenticated user
        
    Returns:
        The newly created chat history with metadata
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            chat_repo = await repo_manager.chat_repo
            
            # Create chat history
            history_data = ChatHistoryCreate(
                title=title,
                user_id=user.id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            # Save to database
            history = await chat_repo.create_chat_history(
                chat_data=history_data,
                session=session
            )
            
            # Commit the transaction
            await session.commit()
            
            logger.info(f"Created new chat history for user {user.id}")
            
            # Convert to response model
            return ChatHistoryResponse(
                id=history.id,
                title=history.title,
                user_id=history.user_id,
                message_count=0,  # New chat has no messages
                created_at=history.created_at,
                updated_at=history.updated_at
            )
            
    except Exception as e:
        logger.error(f"Error creating chat history: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating chat history"
        )

@router.get("", response_model=List[ChatHistoryResponse])
async def get_history_messages(
    request: Request,
    _user: UserInDB = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return")
) -> List[ChatHistoryResponse]:
    """
    Get all chat histories for the authenticated user with pagination.
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        skip: Number of records to skip (for pagination)
        limit: Maximum number of records to return (max 1000)
        
    Returns:
        List of chat histories with metadata
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            chat_repo = await repo_manager.chat_repo
            
            # Get paginated chat histories
            histories = await chat_repo.get_chat_histories(
                user_id=user.id,
                skip=skip,
                limit=limit,
                session=session
            )
            
            # Convert to response models
            return [
                ChatHistoryResponse(
                    id=history.id,
                    title=history.title,
                    user_id=history.user_id,
                    message_count=await chat_repo.get_message_count(history.id, session=session),
                    created_at=history.created_at,
                    updated_at=history.updated_at
                )
                for history in histories
            ]
            
    except Exception as e:
        logger.error(f"Error fetching chat histories for user {user.id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching chat histories"
        )

@router.get("/{history_id}", response_model=Dict[str, Any])
async def get_specific_history_messages(
    request: Request,
    history_id: int = Path(..., description="ID of the chat history"),
    _user: UserInDB = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of messages to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages to return")
) -> Dict[str, Any]:
    """
    Get a specific chat history with its messages.
    
    Args:
        request: The incoming HTTP request
        history_id: ID of the chat history to retrieve
        user: The authenticated user
        skip: Number of messages to skip (for pagination)
        limit: Maximum number of messages to return (max 1000)
        
    Returns:
        Dictionary containing the chat history and its messages
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            chat_repo = await repo_manager.chat_repo
            
            # Get the chat history
            history = await chat_repo.get_chat_history(
                history_id=history_id,
                session=session
            )
            
            # Check if history exists
            if not history:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Chat history not found"
                )
                
            # Check authorization
            if history.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this chat history"
                )
            
            # Get paginated messages
            messages = await chat_repo.get_messages(
                history_id=history_id,
                skip=skip,
                limit=limit,
                session=session
            )
            
            # Get total message count for pagination
            total_messages = await chat_repo.get_message_count(
                history_id=history_id,
                session=session
            )
            
            return {
                "id": history.id,
                "title": history.title,
                "user_id": history.user_id,
                "created_at": history.created_at,
                "updated_at": history.updated_at,
                "messages": messages,
                "pagination": {
                    "total": total_messages,
                    "skip": skip,
                    "limit": limit,
                    "has_more": (skip + len(messages)) < total_messages
                }
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat history {history_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching the chat history"
        )

@router.delete("/{history_id}", status_code=status.HTTP_200_OK)
async def delete_specific_history_messages(
    request: Request,
    history_id: int = Path(..., description="ID of the chat history to delete"),
    _user: UserInDB = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Delete a specific chat history and all its messages.
    
    Args:
        request: The incoming HTTP request
        history_id: ID of the chat history to delete
        user: The authenticated user
        
    Returns:
        Status message indicating success or failure
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            chat_repo = await repo_manager.chat_repo
            
            # First verify the history exists
            history = await chat_repo.get_chat_history(
                history_id=history_id,
                session=session
            )
            
            if not history:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Chat history not found"
                )
                
            # Check authorization
            if history.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to delete this chat history"
                )
            
            # Delete the history and all its messages
            await chat_repo.delete_chat_history(
                history_id=history_id,
                session=session
            )
            
            # Commit the transaction
            await session.commit()
            
            logger.info(f"Deleted chat history {history_id} for user {user.id}")
            
            return {
                "status": "success",
                "message": f"Chat history {history_id} deleted",
                "deleted_id": history_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting chat history {history_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the chat history"
        )

@router.delete("", status_code=status.HTTP_200_OK)
async def delete_all_user_histories(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Delete all chat histories for the authenticated user.
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Status message with count of deleted histories
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            chat_repo = await repo_manager.chat_repo
            
            # Delete all histories for this user
            count = await chat_repo.delete_all_chat_histories(
                user_id=user.id,
                session=session
            )
            
            # Commit the transaction
            await session.commit()
            
            logger.info(f"Deleted all {count} chat histories for user {user.id}")
            
            return {
                "status": "success",
                "message": f"Deleted {count} chat histories",
                "count": count,
                "user_id": str(user.id)
            }
            
    except Exception as e:
        logger.error(f"Error deleting all chat histories for user {user.id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting chat histories"
        )

# Export the router as histories_router for import in app.py
histories_router = router