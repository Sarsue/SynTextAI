from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from typing import Optional
from ..core.utils import get_user_id
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

    user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Route to create a new chat history
@histories_router.post("", status_code=201)
async def create_history(
    title: str = Query(..., description="Title of the chat history"),
    workspace_id: Optional[int] = Query(None, description="Workspace this conversation belongs to"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        # A conversation is answered from one workspace's documents, so it
        # belongs to that workspace. Verify membership rather than trusting the
        # caller's id, or a conversation could be filed into someone else's.
        if workspace_id is not None:
            role = await store.workspace_repo.get_user_role_in_workspace(workspace_id, user_id)
            if role is None:
                raise HTTPException(status_code=403, detail="You do not have access to this workspace.")
        # add_chat_history returns the new id, so returning it unchanged made the
        # endpoint respond with a bare integer. The client reads
        # `createHistoryData?.id` before posting the message, which is undefined
        # on a number, so it silently skipped sending, never cleared its sending
        # state, and the composer sat on "Sending..." forever with no error
        # anywhere: the request that would have failed loudly was never made.
        history_id = await store.chat_repo.add_chat_history(title, user_id, workspace_id)
        if not history_id:
            raise HTTPException(status_code=500, detail="Could not create chat history")
        # messages must be present and empty, not absent. The client renders a
        # conversation with history.messages.map(...), so a payload without the
        # key threw "Cannot read properties of undefined" inside
        # ConversationView, React unmounted the tree, and clicking "New chat"
        # left the user on a blank screen with no error shown anywhere.
        return {
            "id": history_id,
            "title": title,
            "workspace_id": workspace_id,
            "messages": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not create chat history")

# Route to get all chat histories for a user
@histories_router.get("")
async def get_history_messages(
    workspace_id: Optional[int] = Query(None, description="Only conversations in this workspace"),
    organization_id: Optional[int] = Query(None, description="Active organization"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        # The same answer that governs documents and retrieval. Conversations
        # were filtered by "did you create this", so losing access to a
        # workspace left its threads in the sidebar, still readable and still
        # citing documents that would now refuse to open.
        accessible = await store.workspace_repo.accessible_workspace_ids(
            user_id, organization_id=organization_id
        )
        message_list = await store.chat_repo.get_all_user_chat_histories(
            user_id, workspace_id, accessible_workspace_ids=accessible
        )
        return message_list
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error retrieving chat histories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not retrieve chat histories")

# Route to get messages for a specific chat history
@histories_router.get("/messages")
async def get_specific_history_messages(
    history_id: int = Query(..., description="ID of the chat history"),
    organization_id: Optional[int] = Query(None, description="Active organization"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        accessible = await store.workspace_repo.accessible_workspace_ids(
            user_id, organization_id=organization_id
        )
        # Signature is (chat_history_id, user_id). These were passed the other
        # way round, so the ownership check compared a history id against a user
        # id, always failed, and every conversation came back empty while
        # logging "User N attempted to access unauthorized chat history M" with
        # the two values transposed.
        message_list = await store.chat_repo.get_messages_for_chat_history(
            history_id, user_id, accessible_workspace_ids=accessible
        )
        return message_list
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error retrieving messages for history {history_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not retrieve messages for this history")

# Route to delete a specific chat history
@histories_router.delete("", status_code=200)
async def delete_specific_history_messages(
    history_id: int = Query(..., description="ID of the chat history to delete"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        await store.chat_repo.delete_chat_history(user_id, history_id)
        return {"message": "History deleted successfully", "deletedHistoryId": history_id}
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error deleting history {history_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete this history")

# Route to delete all chat histories for a user
@histories_router.delete("/all", status_code=200)
async def delete_all_user_histories(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        await store.chat_repo.delete_all_user_histories(user_id)
        return {"message": "All histories deleted successfully"}
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error deleting all histories for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete histories")