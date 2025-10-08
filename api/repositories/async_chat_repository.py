"""
Async Chat repository for managing chat-related database operations.

This module mirrors the sync ChatRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Dict, Any
import logging

from .async_base_repository import AsyncBaseRepository
from .domain_models import ChatHistory, Message

# Import ORM models from the new models module
from ..models import ChatHistory as ChatHistoryORM
from ..models import Message as MessageORM

# Import SQLAlchemy async components
from sqlalchemy import select, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AsyncChatRepository(AsyncBaseRepository):
    """Async repository for chat operations."""

    async def add_chat_history(self, title: str, user_id: int) -> Optional[int]:
        """Add a new chat history.

        Args:
            title: Title of the chat
            user_id: ID of the user

        Returns:
            Optional[int]: The ID of the newly created chat history, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                chat_orm = ChatHistoryORM(title=title, user_id=user_id)
                session.add(chat_orm)
                await session.flush()
                chat_id = chat_orm.id
                await session.commit()
                logger.info(f"Successfully added chat history {title} for user {user_id}")
                return chat_id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding chat history {title}: {e}", exc_info=True)
                return None

    async def get_latest_chat_history_id(self, user_id: int) -> Optional[int]:
        """Get the latest chat history ID for a user.

        Args:
            user_id: ID of the user

        Returns:
            Optional[int]: Latest chat history ID, or None if no chat histories exist
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(ChatHistoryORM).where(ChatHistoryORM.user_id == user_id).order_by(desc(ChatHistoryORM.id)).limit(1)
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if chat_history:
                    return chat_history.id
                return None

            except Exception as e:
                logger.error(f"Error getting latest chat history for user {user_id}: {e}", exc_info=True)
                return None

    async def add_message(self, content: str, sender: str, user_id: int, chat_history_id: Optional[int] = None) -> Optional[int]:
        """Add a message to a chat history.

        Args:
            content: Content of the message
            sender: Sender of the message (user or assistant)
            user_id: ID of the user
            chat_history_id: ID of the chat history (optional)

        Returns:
            Optional[int]: The ID of the newly created message, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                # If no chat_history_id provided, get the latest one for the user
                if chat_history_id is None:
                    stmt = select(ChatHistoryORM).where(ChatHistoryORM.user_id == user_id).order_by(desc(ChatHistoryORM.id)).limit(1)
                    result = await session.execute(stmt)
                    chat_history = result.scalar_one_or_none()

                    if not chat_history:
                        logger.error(f"No chat history found for user {user_id}")
                        return None

                    chat_history_id = chat_history.id

                message_orm = MessageORM(
                    content=content,
                    sender=sender,
                    user_id=user_id,
                    chat_history_id=chat_history_id
                )
                session.add(message_orm)
                await session.flush()
                message_id = message_orm.id
                await session.commit()

                logger.info(f"Successfully added message for user {user_id} in chat {chat_history_id}")
                return message_id

            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding message for user {user_id}: {e}", exc_info=True)
                return None

    async def get_all_user_chat_histories(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chat histories for a user.

        Args:
            user_id: ID of the user

        Returns:
            List[Dict[str, Any]]: List of chat history data
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(ChatHistoryORM).where(ChatHistoryORM.user_id == user_id).order_by(desc(ChatHistoryORM.created_at))
                result = await session.execute(stmt)
                chat_histories_orm = result.scalars().all()

                chat_histories = []
                for chat in chat_histories_orm:
                    # Get the latest message for this chat
                    stmt = select(MessageORM).where(MessageORM.chat_history_id == chat.id).order_by(desc(MessageORM.created_at)).limit(1)
                    result = await session.execute(stmt)
                    latest_message = result.scalar_one_or_none()

                    chat_histories.append({
                        'id': chat.id,
                        'title': chat.title,
                        'created_at': chat.created_at,
                        'updated_at': chat.updated_at,
                        'latest_message': latest_message.content if latest_message else None,
                        'latest_message_time': latest_message.created_at if latest_message else None
                    })

                return chat_histories

            except Exception as e:
                logger.error(f"Error getting chat histories for user {user_id}: {e}", exc_info=True)
                return []

    async def get_messages_for_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a specific chat history.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user

        Returns:
            List[Dict[str, Any]]: List of message data
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(MessageORM).where(MessageORM.chat_history_id == chat_history_id).order_by(MessageORM.created_at)
                result = await session.execute(stmt)
                messages_orm = result.scalars().all()

                messages = []
                for message in messages_orm:
                    messages.append({
                        'id': message.id,
                        'content': message.content,
                        'sender': message.sender,
                        'created_at': message.created_at,
                        'chat_history_id': message.chat_history_id
                    })

                return messages

            except Exception as e:
                logger.error(f"Error getting messages for chat history {chat_history_id}: {e}", exc_info=True)
                return []

    async def delete_chat_history(self, user_id: int, history_id: int) -> bool:
        """Delete a chat history and all its messages.

        Args:
            user_id: ID of the user
            history_id: ID of the chat history to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if chat history exists and belongs to user
                stmt = select(ChatHistoryORM).where(
                    and_(ChatHistoryORM.id == history_id, ChatHistoryORM.user_id == user_id)
                )
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if not chat_history:
                    logger.error(f"Chat history {history_id} not found for user {user_id}")
                    return False

                # Delete all messages in the chat history
                await session.execute(
                    MessageORM.__table__.delete().where(MessageORM.chat_history_id == history_id)
                )

                # Delete the chat history
                await session.delete(chat_history)
                await session.commit()

                logger.info(f"Successfully deleted chat history {history_id} for user {user_id}")
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
                # Get all chat history IDs for the user
                stmt = select(ChatHistoryORM.id).where(ChatHistoryORM.user_id == user_id)
                result = await session.execute(stmt)
                chat_history_ids = result.scalars().all()

                # Delete all messages for all chat histories
                for chat_id in chat_history_ids:
                    await session.execute(
                        MessageORM.__table__.delete().where(MessageORM.chat_history_id == chat_id)
                    )

                # Delete all chat histories
                await session.execute(
                    ChatHistoryORM.__table__.delete().where(ChatHistoryORM.user_id == user_id)
                )

                await session.commit()

                logger.info(f"Successfully deleted all chat histories for user {user_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting all chat histories for user {user_id}: {e}", exc_info=True)
                return False

    async def format_user_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """Format chat history for display.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user

        Returns:
            List[Dict[str, str]]: Formatted chat history
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(ChatHistoryORM).where(
                    and_(ChatHistoryORM.id == chat_history_id, ChatHistoryORM.user_id == user_id)
                )
                result = await session.execute(stmt)
                chat_history = result.scalar_one_or_none()

                if not chat_history:
                    return []

                stmt = select(MessageORM).where(MessageORM.chat_history_id == chat_history_id).order_by(MessageORM.created_at)
                result = await session.execute(stmt)
                messages_orm = result.scalars().all()

                formatted_history = []
                for message in messages_orm:
                    formatted_history.append({
                        'sender': message.sender,
                        'content': message.content,
                        'timestamp': message.created_at.isoformat() if message.created_at else None
                    })

                return formatted_history

            except Exception as e:
                logger.error(f"Error formatting chat history {chat_history_id}: {e}", exc_info=True)
                return []
