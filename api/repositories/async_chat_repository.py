"""
Async Chat repository for managing chat-related database operations.

This module mirrors the sync ChatRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Dict, Any
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from .async_base_repository import AsyncBaseRepository
from .domain_models import ChatHistory, Message

# Import ORM models from the new models module
from ..models import ChatHistory as ChatHistoryORM
from ..models import Message as MessageORM

logger = logging.getLogger(__name__)


class AsyncChatRepository(AsyncBaseRepository):
    """Async repository for chat history and message operations."""

    async def add_chat_history(self, title: str, user_id: int) -> Optional[int]:
        """Add a new chat history for a user.

        Args:
            title: Title of the chat history
            user_id: ID of the user who owns this chat history

        Returns:
            int: The ID of the newly created chat history, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_chat_history = ChatHistoryORM(
                    title=title,
                    user_id=user_id
                )
                session.add(new_chat_history)
                await session.flush()  # Flush to get the ID without committing
                await session.refresh(new_chat_history)
                return new_chat_history.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating chat history: {e}", exc_info=True)
                return None

    async def get_latest_chat_history_id(self, user_id: int) -> Optional[int]:
        """Get the ID of the most recent chat history for a user.

        Args:
            user_id: ID of the user

        Returns:
            int: The ID of the most recent chat history, or None if not found
        """
        async with self.get_async_session() as session:
            chat_history = await session.query(ChatHistoryORM).filter(
                ChatHistoryORM.user_id == user_id
            ).order_by(ChatHistoryORM.id.desc()).first()

            return chat_history.id if chat_history else None

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
                    chat_history = await session.query(ChatHistoryORM).filter(
                        ChatHistoryORM.user_id == user_id
                    ).order_by(ChatHistoryORM.id.desc()).first()

                    # If no chat history exists, create one
                    if not chat_history:
                        chat_history = ChatHistoryORM(
                            title="Untitled",
                            user_id=user_id
                        )
                        session.add(chat_history)
                        await session.flush()  # To get the ID

                    chat_history_id = chat_history.id

                # Create the message
                new_message = MessageORM(
                    content=content,
                    sender=sender,
                    user_id=user_id,
                    chat_history_id=chat_history_id
                )
                session.add(new_message)
                await session.flush()  # Flush to get the ID without committing
                await session.refresh(new_message)
                return new_message.id

            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding message: {e}", exc_info=True)
                return None

    async def get_all_user_chat_histories(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chat histories for a user with latest messages.

        Args:
            user_id: ID of the user

        Returns:
            List[Dict]: List of chat histories with metadata
        """
        async with self.get_async_session() as session:
            try:
                chat_histories_orm = await session.query(ChatHistoryORM).filter(
                    ChatHistoryORM.user_id == user_id
                ).all()

                result = []
                for ch in chat_histories_orm:
                    # Get the latest message for this chat history
                    latest_message = await session.query(MessageORM).filter(
                        MessageORM.chat_history_id == ch.id
                    ).order_by(MessageORM.timestamp.desc()).first()

                    history_dict = {
                        "id": ch.id,
                        "title": ch.title,
                        "latest_message": latest_message.content[:50] + "..." if latest_message else "No messages",
                        "timestamp": latest_message.timestamp if latest_message else None
                    }
                    result.append(history_dict)

                return result
            except Exception as e:
                logger.error(f"Error getting chat histories: {e}", exc_info=True)
                return []

    async def get_messages_for_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a specific chat history.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user (for security check)

        Returns:
            List[Dict]: List of messages
        """
        async with self.get_async_session() as session:
            try:
                # First verify the user owns this chat history
                chat_history = await session.query(ChatHistoryORM).filter(
                    ChatHistoryORM.id == chat_history_id,
                    ChatHistoryORM.user_id == user_id
                ).first()

                if not chat_history:
                    logger.warning(f"User {user_id} attempted to access unauthorized chat history {chat_history_id}")
                    return []

                # Get messages
                messages_orm = await session.query(MessageORM).filter(
                    MessageORM.chat_history_id == chat_history_id
                ).order_by(MessageORM.timestamp).all()

                result = []
                for msg in messages_orm:
                    message_dict = {
                        "id": msg.id,
                        "content": msg.content,
                        "sender": msg.sender,
                        "timestamp": msg.timestamp.isoformat()
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
                # First verify the user owns this chat history
                chat_history = await session.query(ChatHistoryORM).filter(
                    ChatHistoryORM.id == history_id,
                    ChatHistoryORM.user_id == user_id
                ).first()

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
                logger.error(f"Error deleting chat history: {e}", exc_info=True)
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
                histories = await session.query(ChatHistoryORM).filter(
                    ChatHistoryORM.user_id == user_id
                ).all()

                # Delete each one (cascade will delete messages)
                for history in histories:
                    await session.delete(history)

                await session.commit()
                logger.info(f"Deleted all chat histories for user {user_id}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting chat histories: {e}", exc_info=True)
                return False

    async def format_user_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """Format chat history in a way suitable for LLM context.

        Args:
            chat_history_id: ID of the chat history
            user_id: ID of the user

        Returns:
            List[Dict]: List of messages formatted as {"role": "user"|"assistant", "content": "message"}
        """
        async with self.get_async_session() as session:
            try:
                # First verify the user owns this chat history
                chat_history = await session.query(ChatHistoryORM).filter(
                    ChatHistoryORM.id == chat_history_id,
                    ChatHistoryORM.user_id == user_id
                ).first()

                if not chat_history:
                    logger.warning(f"User {user_id} attempted to access unauthorized chat history {chat_history_id}")
                    return []

                # Get messages
                messages_orm = await session.query(MessageORM).filter(
                    MessageORM.chat_history_id == chat_history_id
                ).order_by(MessageORM.timestamp).all()

                formatted_messages = []
                for msg in messages_orm:
                    role = "user" if msg.sender.lower() == "user" else "assistant"
                    formatted_messages.append({
                        "role": role,
                        "content": msg.content
                    })

                return formatted_messages
            except Exception as e:
                logger.error(f"Error formatting chat history: {e}", exc_info=True)
                return []
