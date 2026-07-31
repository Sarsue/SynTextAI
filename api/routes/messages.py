from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Header, BackgroundTasks, Request
from typing import List
from ..core.utils import get_user_id
from ..repositories.repository_manager import RepositoryManager
from ..services.llm_service import get_text_embedding
from ..core.rate_limit import limiter, CHAT_RATE_LIMIT
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

    user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Route to create a new message
@messages_router.post("", status_code=201)
@limiter.limit(CHAT_RATE_LIMIT)
async def create_message(
    request: Request,
    background_tasks: BackgroundTasks,
    message: str = Query(..., description="The message content"),
    language: str = Query("English", description="Language of the message"),
    comprehension_level: str = Query("beginner", description="Comprehension level of the message"),
    history_id: int = Query(..., description="ID of the chat history"),
    workspace_id: int | None = Query(None, description="Optional workspace ID to scope retrieval"),
    file_id: int | None = Query(None, description="Optional file ID to scope retrieval"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]

        # Verify the caller actually owns history_id / workspace_id / file_id before
        # touching them — these are attacker-controlled query params, and without this
        # check a user could inject messages into (and get RAG answers grounded in)
        # another user's chat history, workspace, or documents.
        if not await store.chat_repo.user_owns_chat_history(history_id, user_id):
            raise HTTPException(status_code=404, detail="Chat history not found")

        # One question, asked the same way everywhere: which workspaces may
        # this person see? This used to call list_workspaces_for_user, which
        # answers "what do you own or were assigned" — a member with
        # organization-wide reach has neither, so they could not send a message
        # at all.
        accessible = await store.workspace_repo.accessible_workspace_ids(user_id)

        if workspace_id is not None and workspace_id not in accessible:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if file_id is not None:
            file_record = await store.file_repo.get_file_by_id(file_id)
            # Authorized by the document's workspace, not by who uploaded it.
            # Checking the uploader refused a member asking about a document
            # the owner had put in a workspace they can read.
            if not file_record or file_record.get("workspace_id") not in accessible:
                raise HTTPException(status_code=404, detail="File not found")

        # Save the user message to the history
        message_id = await store.chat_repo.add_message(
            content=message, sender='user', user_id=user_id, chat_history_id=history_id
        )
        if not message_id:
            raise HTTPException(status_code=500, detail="Could not save message")

        # add_message returns the new id, and returning [id] handed the client a
        # list of bare integers where it expected message objects. It read
        # .content and .timestamp off a number, got undefined, and
        # new Date(undefined).toISOString() threw, so sendMessage aborted before
        # rendering anything: the sent message never appeared, the conversation
        # stayed empty, and the failure looked like the answer never arriving.
        message_list = [{
            "id": message_id,
            "content": message,
            "sender": "user",
            "timestamp": datetime.utcnow().isoformat(),
        }]

        await store.agent_run_repo.enqueue_run(
            run_type="answer_query",
            agent_name="QueryAgent",
            agent_version=None,
            payload={
                "user_id": int(user_id),
                "history_id": int(history_id),
                "message": message,
                "language": language,
                "comprehension_level": comprehension_level,
                "workspace_id": int(workspace_id) if workspace_id is not None else None,
                "file_id": int(file_id) if file_id is not None else None,
            },
            user_id=int(user_id),
            chat_history_id=int(history_id),
            workspace_id=int(workspace_id) if workspace_id is not None else None,
            file_id=int(file_id) if file_id is not None else None,
            priority=10,
            max_attempts=3,
        )
        logger.info(f"Enqueued agent run for query processing history_id={history_id}")

        return message_list
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not create message")