"""
Async Chat repository for managing chat-related database operations.

This module mirrors the sync ChatRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Dict, Any
import logging
from sqlalchemy.exc import IntegrityError

from .async_base_repository import AsyncBaseRepository

# Import ORM models from the new models module
from ..models import ChatHistory as ChatHistoryORM
from ..models import Message as MessageORM

# Import SQLAlchemy async components
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

class AsyncChatRepository(AsyncBaseRepository):
    """Async repository for chat operations."""

    async def add_chat_history(self, title: str, user_id: int, workspace_id: Optional[int] = None) -> Optional[int]:
        """Add a new chat history for a user, in a workspace.

        Args:
            title: Title of the chat history
            user_id: ID of the user who owns this chat history
            workspace_id: Workspace the conversation belongs to. Answers are
                retrieved from this workspace's documents, so the conversation
                is only meaningful alongside them.

        Returns:
            int: The ID of the newly created chat history, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_chat_history = ChatHistoryORM(
                    title=title,
                    user_id=user_id,
                    workspace_id=workspace_id
                )
                session.add(new_chat_history)
                await session.flush()
                chat_id = new_chat_history.id
                await session.commit()
                logger.info(f"Added new chat history {title} (ID: {chat_id}) for user {user_id}")
                return chat_id
            except IntegrityError as e:
                await session.rollback()
                logger.error(f"Integrity error creating chat history: {e}", exc_info=True)
                return None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating chat history: {e}", exc_info=True)
                return None

    async def add_message(self, content: str, sender: str, user_id: int, chat_history_id: Optional[int] = None) -> Optional[int]:
        """Add a new message to a chat history.

        Args:
            content: Message content
            sender: Message sender (user or assistant)
            user_id: ID of the user
            chat_history_id: ID of the chat history, if None, uses the latest one

        Returns:
            int: The ID of the newly created message, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                # If no chat history ID was provided, get the latest one
                if chat_history_id is None:
                    stmt = select(ChatHistoryORM).where(
                        ChatHistoryORM.user_id == user_id
                    ).order_by(desc(ChatHistoryORM.id)).limit(1)
                    result = await session.execute(stmt)
                    chat_history = result.scalar_one_or_none()

                    # If no chat history exists, create one
                    if not chat_history:
                        chat_history = ChatHistoryORM(
                            title="Untitled",
                            user_id=user_id
                        )
                        session.add(chat_history)
                        await session.flush()
                        chat_history_id = chat_history.id
                    else:
                        chat_history_id = chat_history.id

                # Create the message
                new_message = MessageORM(
                    content=content,
                    sender=sender,
                    user_id=user_id,
                    chat_history_id=chat_history_id
                )
                session.add(new_message)
                await session.flush()
                message_id = new_message.id
                await session.commit()
                logger.info(f"Added message (ID: {message_id}) to chat history {chat_history_id} for user {user_id}")
                return message_id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding message: {e}", exc_info=True)
                return None

    async def get_all_user_chat_histories(
        self,
        user_id: int,
        workspace_id: Optional[int] = None,
        accessible_workspace_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """A user's conversations, in workspaces they can still see.

        Args:
            user_id: ID of the user
            workspace_id: When given, only conversations in this workspace.
            accessible_workspace_ids: The workspaces this user may see. This is
                the same answer that governs documents and retrieval, so a
                conversation can never outlive access to the workspace it was
                held in.

        Conversations used to be filtered by "did you create this", which is a
        different question from "may you see this". Losing access to a
        workspace left its conversations in the sidebar, still readable, still
        citing documents that would now refuse to open.

        Returns:
            List[Dict]: List of chat histories with metadata
        """
        async with self.get_async_session() as session:
            try:
                # Query chat histories with their messages
                conditions = [ChatHistoryORM.user_id == user_id]
                if workspace_id is not None:
                    conditions.append(ChatHistoryORM.workspace_id == workspace_id)
                if accessible_workspace_ids is not None:
                    # Conversations predating workspace scoping have no
                    # workspace and stay visible to whoever held them; anything
                    # filed in a workspace is visible only while that workspace
                    # is.
                    conditions.append(
                        or_(
                            ChatHistoryORM.workspace_id.is_(None),
                            ChatHistoryORM.workspace_id.in_(accessible_workspace_ids),
                        )
                    )
                stmt = select(ChatHistoryORM).where(
                    *conditions
                ).options(selectinload(ChatHistoryORM.messages))
                result = await session.execute(stmt)
                chat_histories_orm = result.scalars().all()

                result = []
                for ch in chat_histories_orm:
                    # Get the latest message
                    latest_message = None
                    if ch.messages:
                        latest_message = max(ch.messages, key=lambda m: m.timestamp if m.timestamp else m.created_at)

                    history_dict = {
                        "id": ch.id,
                        "title": ch.title,
                        "latest_message": latest_message.content[:50] + "..." if latest_message and latest_message.content else "No messages",
                        "timestamp": latest_message.timestamp.isoformat() if latest_message and latest_message.timestamp else None
                    }
                    result.append(history_dict)

                return result
            except Exception as e:
                logger.error(f"Error getting chat histories: {e}", exc_info=True)
                return []

    async def user_owns_chat_history(self, chat_history_id: int, user_id: int) -> bool:
        """Check whether chat_history_id exists and belongs to user_id."""
        async with self.get_async_session() as session:
            stmt = select(ChatHistoryORM.id).where(
                and_(ChatHistoryORM.id == chat_history_id, ChatHistoryORM.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def get_messages_for_chat_history(
        self,
        chat_history_id: int,
        user_id: int,
        accessible_workspace_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Messages in one conversation, if the caller may still see it.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user
            accessible_workspace_ids: The workspaces this user may see. Having
                written a conversation is not sufficient: access follows the
                workspace, so losing the workspace closes the conversation too.

        Returns:
            List[Dict]: List of messages
        """
        async with self.get_async_session() as session:
            try:
                conditions = [
                    ChatHistoryORM.id == chat_history_id,
                    ChatHistoryORM.user_id == user_id,
                ]
                if accessible_workspace_ids is not None:
                    conditions.append(
                        or_(
                            ChatHistoryORM.workspace_id.is_(None),
                            ChatHistoryORM.workspace_id.in_(accessible_workspace_ids),
                        )
                    )
                stmt = select(ChatHistoryORM).where(and_(*conditions))
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if not chat_history:
                    logger.warning(f"User {user_id} attempted to access unauthorized chat history {chat_history_id}")
                    return []

                # Get messages
                stmt = select(MessageORM).where(
                    MessageORM.chat_history_id == chat_history_id
                ).order_by(MessageORM.timestamp)
                result = await session.execute(stmt)
                messages_orm = result.scalars().all()

                result = []
                for msg in messages_orm:
                    message_dict = {
                        "id": msg.id,
                        "content": msg.content,
                        "sender": msg.sender,
                        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
                    }
                    result.append(message_dict)

                return result
            except Exception as e:
                logger.error(f"Error getting messages: {e}", exc_info=True)
                return []

    async def delete_chat_history(self, user_id: int, history_id: int) -> bool:
        """Delete a chat history and all associated messages.

        Args:
            user_id: ID of the user
            history_id: ID of the chat history to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Verify user owns the chat history
                stmt = select(ChatHistoryORM).where(
                    and_(ChatHistoryORM.id == history_id, ChatHistoryORM.user_id == user_id)
                )
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if not chat_history:
                    logger.warning(f"User {user_id} attempted to delete unauthorized chat history {history_id}")
                    return False

                # Delete the chat history (cascade will delete messages)
                await session.delete(chat_history)
                await session.commit()
                logger.info(f"Deleted chat history {history_id} for user {user_id}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting chat history {history_id}: {e}", exc_info=True)
                return False

    async def delete_all_user_histories(self, user_id: int) -> bool:
        """Delete all chat histories for a user.

        Args:
            user_id: ID of the user

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Query all histories for this user
                stmt = select(ChatHistoryORM).where(ChatHistoryORM.user_id == user_id)
                result = await session.execute(stmt)
                histories = result.scalars().all()

                # Delete each history (cascade will delete messages)
                for history in histories:
                    await session.delete(history)

                await session.commit()
                logger.info(f"Deleted all chat histories for user {user_id}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting chat histories: {e}", exc_info=True)
                return False

    async def format_user_chat_history(
        self,
        chat_history_id: int,
        user_id: int,
        accessible_workspace_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, str]]:
        """Format chat history in a way suitable for LLM context.

        The workspace guard below referenced `accessible_workspace_ids` while
        the signature never took it, so every call raised NameError, was caught
        by the handler at the bottom, and returned an empty list. Conversation
        history has therefore never reached the model: every question was
        answered as if it were the first, and a follow-up like "what about for
        injuries?" had nothing to resolve "that" against.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user
            accessible_workspace_ids: Restrict to conversations in these
                workspaces. Optional, because a caller that has not resolved
                them still gets the user_id check, which is the guard that
                keeps one customer out of another's conversations.

        Returns:
            List[Dict]: List of messages formatted as {"role": "user"|"assistant", "content": "message"}
        """
        async with self.get_async_session() as session:
            try:
                conditions = [
                    ChatHistoryORM.id == chat_history_id,
                    ChatHistoryORM.user_id == user_id,
                ]
                if accessible_workspace_ids is not None:
                    conditions.append(
                        or_(
                            ChatHistoryORM.workspace_id.is_(None),
                            ChatHistoryORM.workspace_id.in_(accessible_workspace_ids),
                        )
                    )
                stmt = select(ChatHistoryORM).where(and_(*conditions))
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if not chat_history:
                    logger.warning(f"User {user_id} attempted to access unauthorized chat history {chat_history_id}")
                    return []

                # Get messages
                stmt = select(MessageORM).where(
                    MessageORM.chat_history_id == chat_history_id
                ).order_by(MessageORM.timestamp)
                result = await session.execute(stmt)
                messages_orm = result.scalars().all()

                formatted_messages = []
                for msg in messages_orm:
                    role = "user" if msg.sender.lower() == "user" else "assistant"
                    formatted_messages.append({
                        "role": role,
                        "content": msg.content
                    })

                return formatted_messages
            except Exception as e:
                logger.error(f"Error formatting chat history {chat_history_id}: {e}", exc_info=True)
                return []