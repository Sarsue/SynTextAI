from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, Header, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Any
import logging
import os
from datetime import datetime

from ..repositories import RepositoryManager, get_repository_manager
from ..models.orm_models import Message
from ..models.chat import Message as MessageSchema, MessageCreate
from ..services.agent_service import agent_service
from ..dependencies import authenticate_user, get_repository_manager as get_repo_manager, get_request_store, get_repository_manager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
messages_router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

# Get store from request (for routes that can't use async dependency injection)
def get_store(request: Request):
    return get_request_store(request)

# Get repository manager dependency
async def get_repo_manager() -> RepositoryManager:
    return await get_repository_manager()

# Route to create a new message
async def process_message_with_qa_agent(
    user_id: str,
    history_id: int,
    message: str,
    language: str,
    comprehension_level: str,
    repo_manager: RepositoryManager
):
    """Process a user message using the QAAgent."""
    try:
        # Get conversation history in formatted form
        formatted_history = await repo_manager.chat_repo.format_user_chat_history(history_id, user_id)
        
        # Use the QAAgent to process the message
        response = await agent_service.process_content(
            agent_name="qa",
            content={
                "question": message,
                "chat_history": formatted_history,
                "language": language,
                "comprehension_level": comprehension_level
            },
            content_type="json"
        )
        
        # Save the bot's response to the history
        bot_response = await repo_manager.chat_repo.add_message_to_chat(
            chat_id=history_id,
            content=response.get("answer", "I couldn't process your request. Please try again."),
            sender='bot',
            user_id=user_id
        )
        
        return bot_response
        
    except Exception as e:
        logger.error(f"Error processing message with QAAgent: {e}", exc_info=True)
        try:
# Save error message to chat history
            error_msg = await repo_manager.chat_repo.add_message_to_chat(
                chat_id=history_id,
                content="Sorry, I encountered an error processing your message. Please try again.",
                sender='bot',
                user_id=user_id
            )
            return error_msg
        except Exception as inner_e:
            logger.error(f"Failed to save error message: {inner_e}", exc_info=True)
            raise

@messages_router.post("", response_model=MessageSchema, status_code=status.HTTP_201_CREATED)
async def create_message(
    message_data: MessageCreate,
    background_tasks: BackgroundTasks,
    comprehension_level: str = Query("beginner", description="Comprehension level (beginner, intermediate, advanced)"),
    history_id: int = Query(..., description="ID of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
):
    """Create a new message and process it using the QAAgent."""
    try:
        user_id = user_data["user_id"]
        message_content = message_data.message
        language = message_data.language if hasattr(message_data, 'language') else 'en'

        # Verify the chat history exists and belongs to the user
        chat = await repo_manager.chat_repo.get_chat_with_messages(history_id)
        if not chat or chat.user_id != user_id:
            raise HTTPException(status_code=404, detail="Chat history not found")

        # Save the user message to the history
        user_message = await repo_manager.chat_repo.add_message_to_chat(
            chat_id=history_id,
            content=message_content,
            sender='user',
            user_id=user_id
        )
        
        # Process the message in the background
        background_tasks.add_task(
            process_message_with_qa_agent,
            user_id,
            history_id,
            message_content,
            language,
            comprehension_level,
            repo_manager
        )
        
        logger.info(f"Enqueued message for processing: {message_content[:100]}...")
        return user_message
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process message")