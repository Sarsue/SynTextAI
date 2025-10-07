"""
Async Chat Repository implementation.
Handles all database operations for Chat and Message models using async SQLAlchemy.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy import select, update, delete, or_
from sqlalchemy.orm import selectinload, joinedload
import logging

from .async_base_repository import AsyncBaseRepository
from ..models.orm_models import ChatHistory as ChatHistoryORM, Message as MessageORM, File as FileORM
from .domain_models import ChatHistory, Message
logger = logging.getLogger(__name__)

class AsyncChatRepository(AsyncBaseRepository[ChatHistoryORM, Any, Any]):
    """
    Async repository for ChatHistory and Message model operations.
    """
    # Define the model class attribute required by AsyncBaseRepository
    model = ChatHistory
    
    def __init__(self, repository_manager, session_factory=None):
        """
        Initialize the chat repository.
        
        Args:
            repository_manager: The repository manager instance
            session_factory: Optional SQLAlchemy async session factory
        """
        super().__init__(repository_manager, session_factory)
        self._initialized = True
    
    async def get_chat_with_messages(
        self, 
        chat_id: int,
        include_messages: bool = True,
        message_limit: int = 100,
        message_offset: int = 0
    ) -> Optional[ChatHistory]:
        async with self.session_scope() as session:
            stmt = select(ChatHistory).where(ChatHistory.id == chat_id)
            
            if include_messages:
                stmt = stmt.options(
                    selectinload(ChatHistory.messages)
                    .limit(message_limit)
                    .offset(message_offset)
                )
            
            result = await session.execute(stmt)
            return result.scalars().first()
    
    async def get_user_chats(
        self, 
        user_id: int, 
        skip: int = 0, 
        limit: int = 50,
        include_messages: bool = False,
        message_limit: int = 5
    ) -> List[ChatHistory]:
        async with self._repository_manager.session_scope() as session:
            stmt = (
                select(ChatHistory)
                .where(ChatHistory.user_id == user_id)
                .order_by(ChatHistory.updated_at.desc())
                .offset(skip)
                .limit(limit)
            )
            
            if include_messages:
                stmt = stmt.options(selectinload(ChatHistory.messages))
            
            result = await session.execute(stmt)
            chats = result.scalars().all()
            
            # Trim messages in Python since SQLAlchemy doesn't allow limit on relationship loads
            if include_messages:
                for chat in chats:
                    chat.messages = sorted(chat.messages, key=lambda m: m.timestamp, reverse=True)[:message_limit]
            
            return chats
    
    async def get_chat_messages(
        self, 
        chat_id: int, 
        skip: int = 0, 
        limit: int = 100,
        include_deleted: bool = False
    ) -> List[Message]:
        async with self._repository_manager.session_scope() as session:
            stmt = (
                select(Message)
                .where(Message.chat_history_id == chat_id)
                .order_by(Message.timestamp.asc())
                .offset(skip)
                .limit(limit)
            )
            if not include_deleted:
                stmt = stmt.where(Message.is_active == True)
            result = await session.execute(stmt)
            return result.scalars().all()
    
    async def add_message_to_chat(
        self, 
        chat_id: int, 
        content: str, 
        sender: str,
        user_id: int
    ) -> Message:
        async with self._repository_manager.session_scope() as session:
            message = Message(
                chat_history_id=chat_id,
                content=content,
                sender=sender,
                user_id=user_id,
                timestamp=datetime.utcnow()
            )
            session.add(message)
            await session.flush()
            await session.refresh(message)
            return message
    
    async def update_chat_title(self, chat_id: int, title: str) -> Optional[ChatHistory]:
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(
                update(ChatHistory)
                .where(ChatHistory.id == chat_id)
                .values(title=title)
                .returning(ChatHistory)
            )
            return result.scalars().first()
    
    async def search_chats(
        self, 
        user_id: int, 
        query: str,
        limit: int = 10
    ) -> List[ChatHistory]:
        async with self._repository_manager.session_scope() as session:
            # Search in chat titles
            chat_results = await session.execute(
                select(ChatHistory)
                .where(ChatHistory.user_id == user_id, ChatHistory.title.ilike(f"%{query}%"))
                .order_by(ChatHistory.id.desc())
                .limit(limit)
            )
            # Search in messages
            message_results = await session.execute(
                select(ChatHistory)
                .join(ChatHistory.messages)
                .where(ChatHistory.user_id == user_id, Message.content.ilike(f"%{query}%"))
                .order_by(Message.timestamp.desc())
                .limit(limit)
            )
            chats = {chat.id: chat for chat in chat_results.scalars().all()}
            for chat in message_results.scalars().all():
                chats[chat.id] = chat
            return list(chats.values())
    
    async def delete_chat(self, chat_id: int) -> bool:
        async with self._repository_manager.session_scope() as session:
            await session.execute(delete(Message).where(Message.chat_history_id == chat_id))
            await session.execute(delete(ChatHistory).where(ChatHistory.id == chat_id))
            return True
    
    async def get_chat_with_file(self, chat_id: int) -> Optional[ChatHistory]:
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(
                select(ChatHistory)
                .options(joinedload(ChatHistory.file))
                .where(ChatHistory.id == chat_id)
            )
            return result.unique().scalars().first()
            
    async def format_user_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """
        Format chat history in a way suitable for LLM context.
        
        Args:
            chat_history_id: ID of the chat history to format
            user_id: ID of the user making the request (for authorization)
            
        Returns:
            List of message dictionaries with role and content
        """
        # First verify the user has access to this chat history
        chat = await self.get_chat_with_messages(chat_history_id)
        if not chat or chat.user_id != user_id:
            return []
            
        # Format messages for the LLM
        formatted_messages = [
            {
                "role": message.role,
                "content": message.content
            }
            for message in sorted(chat.messages, key=lambda m: m.timestamp)
        ]
            
        return formatted_messages
