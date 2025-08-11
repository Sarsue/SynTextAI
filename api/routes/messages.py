from fastapi import APIRouter, Depends, HTTPException, Query, Header, BackgroundTasks, Request
from typing import Dict
import logging
from ..utils import get_user_id
from ..repositories.repository_manager import RepositoryManager
from ..services.agent_service import agent_service

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
messages_router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

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

# Route to create a new message
async def process_message_with_qa_agent(
    user_id: str,
    history_id: int,
    message: str,
    language: str,
    comprehension_level: str,
    store: RepositoryManager
):
    """Process a user message using the QAAgent."""
    try:
        # Get conversation history in formatted form
        formatted_history = store.format_user_chat_history(history_id, user_id)
        
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
        bot_response = store.add_message(
            content=response.get("answer", "I couldn't process your request. Please try again."),
            sender='bot',
            user_id=user_id,
            chat_history_id=history_id
        )
        
        return bot_response
        
    except Exception as e:
        logger.error(f"Error processing message with QAAgent: {e}", exc_info=True)
        # Save error message to chat history
        error_msg = store.add_message(
            content="Sorry, I encountered an error processing your message. Please try again.",
            sender='bot',
            user_id=user_id,
            chat_history_id=history_id
        )
        return error_msg

@messages_router.post("", status_code=201)
async def create_message(
    background_tasks: BackgroundTasks,
    message: str = Query(..., description="The message content"),
    language: str = Query("en", description="Language code (e.g., 'en', 'es')"),
    comprehension_level: str = Query("beginner", description="Comprehension level (beginner, intermediate, advanced)"),
    history_id: int = Query(..., description="ID of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Create a new message and process it using the QAAgent."""
    try:
        user_id = user_data["user_id"]

        # Save the user message to the history
        user_message = store.add_message(
            content=message,
            sender='user',
            user_id=user_id,
            chat_history_id=history_id
        )
        
        # Process the message in the background
        background_tasks.add_task(
            process_message_with_qa_agent,
            user_id,
            history_id,
            message,
            language,
            comprehension_level,
            store
        )
        
        logger.info(f"Enqueued message for processing: {message[:100]}...")
        return [user_message]
        
    except Exception as e:
        logger.error(f"Error creating message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))