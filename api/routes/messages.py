from fastapi import APIRouter, Depends, HTTPException, Query, Header, BackgroundTasks, Request
from typing import List
from utils import get_user_id
from repositories.repository_manager import RepositoryManager
from llm_service import get_text_embedding
import logging
from typing import Dict
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
@messages_router.post("", status_code=201)
async def create_message(
    background_tasks: BackgroundTasks,
    message: str = Query(..., description="The message content"),
    language: str = Query("English", description="Language of the message"),
    comprehension_level: str = Query("beginner", description="Comprehension level of the message"),
    history_id: int = Query(..., description="ID of the chat history"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        from tasks import process_query_data
        user_id = user_data["user_id"]

        # Save the user message to the history
        user_request = store.add_message(
            content=message, sender='user', user_id=user_id, chat_history_id=history_id
        )
        message_list = [user_request]

        # Enqueue the task for processing the query
        background_tasks.add_task(process_query_data, user_id, history_id, message, language, comprehension_level)
        logger.info(f"Enqueued Task for processing {message}")

        return message_list
    except Exception as e:
        logger.error(f"Error creating message: {e}")
        raise HTTPException(status_code=500, detail=str(e))