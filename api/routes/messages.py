from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..models.chat import Message, MessageCreate, MessageUpdate
from typing import List

# Define response models
class MessageResponse(Message):
    """Response model for a single message."""
    pass

class MessageListResponse(BaseModel):
    """Response model for a list of messages."""
    messages: List[Message]
    total: int
from ..repositories import AsyncChatRepository
from ..dependencies import get_repository_manager
from ..repositories.domain_models import UserInDB, ChatHistory, Message
from ..middleware.auth import get_current_user
from ..services.agent_service import agent_service

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

class ProcessedMessageResponse(MessageResponse):
    """Response model for a processed message with agent response."""
    agent_response: str = Field(..., description="The agent's response to the message")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="Sources used to generate the response")

async def process_message_with_qa_agent(
    user_id: str,
    history_id: int,
    message: str,
    language: str = "en",
    comprehension_level: str = "intermediate",
) -> Dict[str, Any]:
    """
    Process a user message using the QAAgent.
    
    Args:
        user_id: ID of the user sending the message
        history_id: ID of the chat history
        message: The user's message content
        language: Language code (default: "en")
        comprehension_level: User's comprehension level (beginner, intermediate, advanced)
        
    Returns:
        Dictionary containing the agent's response and any sources used
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Get chat repository and format history
            chat_repo = await repo_manager.chat_repo
            formatted_history = await chat_repo.format_user_chat_history(
                history_id=history_id,
                user_id=user_id,
                session=session
            )
            
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
        bot_response = await chat_repo.add_message_to_chat(
            chat_id=history_id,
            content=response.get("answer", "I couldn't process your request. Please try again."),
            sender='bot',
            user_id=user_id
        )
        
        return bot_response
        
    except Exception as e:
        logger.error(f"Error processing message with QAAgent: {e}", exc_info=True)
        try:
            # Get chat repository and save error message to chat history
            chat_repo = await repo_manager.chat_repo
            error_msg = await chat_repo.add_message_to_chat(
                chat_id=history_id,
                content="Sorry, I encountered an error processing your message. Please try again.",
                sender='bot',
                user_id=user_id
            )
            return error_msg
        except Exception as inner_e:
            logger.error(f"Failed to save error message: {inner_e}", exc_info=True)
            raise

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProcessedMessageResponse)
async def create_message(
    request: Request,
    message_data: MessageCreate,
    background_tasks: BackgroundTasks,
    _user: UserInDB = Depends(get_current_user),
    history_id: int = Query(..., description="ID of the chat history"),
    comprehension_level: str = Query("intermediate", description="Comprehension level (beginner, intermediate, advanced)")
) -> ProcessedMessageResponse:
    """
    Create a new message in the specified chat history and get an AI response.
    
    The user's message is saved to the database, and the AI's response is generated
    asynchronously. The initial response includes a placeholder that will be updated
    once the AI processing is complete.
    
    Args:
        message_data: The message data including content and metadata
        background_tasks: FastAPI background tasks for async processing
        history_id: ID of the chat history this message belongs to
        comprehension_level: User's preferred comprehension level
        user: The authenticated user
        
    Returns:
        The created message with the AI's response
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Verify the chat history exists and belongs to the user
            chat_repo = await repo_manager.chat_repo
            history = await chat_repo.get_chat_history(
                history_id=history_id,
                session=session
            )
            
            if not history:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Chat history not found"
                )
                
            if history.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to add messages to this chat history"
                )
            
            # Save the user's message to the database
            user_message = await chat_repo.add_message_to_chat(
                chat_id=history_id,
                content=message_data.content,
                role="user",
                user_id=user.id,
                metadata={
                    "comprehension_level": comprehension_level,
                    "language": message_data.language or "en"
                },
                session=session
            )
            
            # Process the message with the QA agent in the background
            background_tasks.add_task(
                process_message_with_qa_agent,
                user_id=user.id,
                history_id=history_id,
                message=message_data.content,
                language=message_data.language or "en",
                comprehension_level=comprehension_level
            )
            
            # Return a placeholder response that will be updated later
            return ProcessedMessageResponse(
                id=user_message.id,
                content=user_message.content,
                role=user_message.role,
                created_at=user_message.created_at,
                updated_at=user_message.updated_at,
                metadata=user_message.metadata or {},
                agent_response="Processing your message...",
                sources=[]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing your message"
        )

# Export the router
messages_router = router