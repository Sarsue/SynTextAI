from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Request
from typing import List
from ..core.utils import get_user_id
from ..repositories.repository_manager import RepositoryManager
from ..core.rate_limit import limiter, CHAT_RATE_LIMIT
from ..core.limits import assert_can_ask
from ..core.auth import authenticate_user, get_store
from ..core.log_safety import safe_text
import logging
from typing import Dict
# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
messages_router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

class MessageBody(BaseModel):
    """The question, in the body where it belongs.

    It used to arrive as a query parameter, which put every question a customer
    ever asked into the access log in full:

        POST /api/v1/messages?message=what%20is%20Bolanle%20Okonkwo%20diagnosis

    Redacting application logs does nothing about that, and neither does
    anything else we control, because a URL is copied into places we never see:
    the reverse proxy, the CDN, the browser's history, the Referer header sent
    to any third party the page later talks to. For a dental or legal practice
    the question is usually the most sensitive string in the request.

    """
    message: str
    language: str = "English"
    comprehension_level: str = "beginner"
    history_id: int | None = None
    workspace_id: int | None = None
    file_id: int | None = None


# The chips the thumbs-down form offers. Validated here rather than trusted,
# so the stored set stays countable: free text is where the insight is, but a
# free-text "reason" would make the tally meaningless.
FEEDBACK_REASONS = {"wrong", "incomplete", "not_in_documents", "wrong_source"}

# Long enough for a sentence about what went wrong, short enough that this
# never becomes a second place customer content accumulates.
MAX_FEEDBACK_COMMENT = 500


class FeedbackBody(BaseModel):
    """A rating of one answer.

    Defined above the decorators, not between a decorator and its function.
    Putting a class there once applied @messages_router.post to the class
    instead of the handler and the app crash-looped on start.
    """
    rating: int
    reason: str | None = None
    comment: str | None = None


# Route to create a new message
@messages_router.post("", status_code=201)
@limiter.limit(CHAT_RATE_LIMIT)
async def create_message(
    request: Request,
    background_tasks: BackgroundTasks,
    body: MessageBody,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        message = body.message
        language = body.language
        comprehension_level = body.comprehension_level
        history_id = body.history_id
        workspace_id = body.workspace_id
        file_id = body.file_id
        if not message or history_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="message and history_id are required",
            )

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

        # Paid for? Checked here, after the ownership checks above and before
        # anything is written or queued, so an unpaid caller is refused without
        # storing a message or spending a model call. Deliberately not first: a
        # history that belongs to somebody else should answer 404 whether or not
        # the asker is paying, rather than confirming it exists.
        await assert_can_ask(store, user_id, workspace_id=workspace_id)

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

@messages_router.put("/{message_id}/feedback", status_code=200)
async def set_feedback(
    message_id: int,
    body: FeedbackBody,
    organization_id: int | None = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Record what somebody thought of an answer.

    A PUT because it is idempotent: pressing thumbs-down and then thumbs-up
    replaces the rating rather than adding a second one. The unique constraint
    on (message_id, user_id) is what makes that true underneath.

    Deliberately not gated on entitlement. An organization that has stopped
    paying cannot ask anything new, but telling us an old answer was wrong is
    the last thing we should refuse: it is the only signal we get from somebody
    on their way out.
    """
    user_id = user_data["user_id"]

    if body.rating not in (-1, 1):
        raise HTTPException(status_code=422, detail="rating must be -1 or 1")

    reason = (body.reason or "").strip() or None
    if reason is not None and reason not in FEEDBACK_REASONS:
        raise HTTPException(status_code=422, detail="unknown reason")

    comment = (body.comment or "").strip() or None
    if comment is not None and len(comment) > MAX_FEEDBACK_COMMENT:
        raise HTTPException(
            status_code=422,
            detail=f"comment must be {MAX_FEEDBACK_COMMENT} characters or fewer",
        )

    # Same reach question the rest of the app asks, not a new one.
    accessible = await store.workspace_repo.accessible_workspace_ids(
        user_id, organization_id=organization_id
    )

    saved = await store.chat_repo.set_message_feedback(
        message_id=message_id,
        user_id=user_id,
        rating=body.rating,
        reason=reason,
        comment=comment,
        accessible_workspace_ids=accessible,
    )

    if saved is None:
        # Not theirs, or not there. One answer for both, so this cannot be used
        # to discover which message ids exist.
        raise HTTPException(status_code=404, detail="Message not found")
    if saved.get("error") == "not_an_answer":
        raise HTTPException(status_code=400, detail="Only answers can be rated")

    # The comment is customer content and never reaches the log line.
    logger.info(
        "Feedback %s on message %s by user %s (reason=%s, comment=%s)",
        saved["rating"], message_id, user_id, saved["reason"],
        safe_text(saved["comment"], "c") if saved["comment"] else "none",
    )
    return saved


@messages_router.delete("/{message_id}/feedback", status_code=200)
async def clear_feedback(
    message_id: int,
    organization_id: int | None = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Undo a rating: pressing the same thumb again means "never mind"."""
    user_id = user_data["user_id"]

    accessible = await store.workspace_repo.accessible_workspace_ids(
        user_id, organization_id=organization_id
    )
    ok = await store.chat_repo.clear_message_feedback(
        message_id=message_id,
        user_id=user_id,
        accessible_workspace_ids=accessible,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"rating": None}
