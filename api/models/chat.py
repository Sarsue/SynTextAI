"""
Chat-related Pydantic models/schemas.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from .user import User

class MessageBase(BaseModel):
    """Base message model with common fields."""
    content: str
    sender: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    user_id: int
    chat_history_id: int

class MessageCreate(MessageBase):
    """Schema for creating a new message."""
    pass

class MessageUpdate(BaseModel):
    """Schema for updating a message."""
    content: Optional[str] = None

class Message(MessageBase):
    """Message model for API responses."""
    id: int
    
    class Config:
        from_attributes = True

class ChatHistoryBase(BaseModel):
    """Base chat history model with common fields."""
    title: str = "Untitled"
    user_id: int

class ChatHistoryCreate(ChatHistoryBase):
    """Schema for creating a new chat history."""
    pass

class ChatHistoryUpdate(BaseModel):
    """Schema for updating a chat history."""
    title: Optional[str] = None

class ChatHistory(ChatHistoryBase):
    """Chat history model for API responses with related messages."""
    id: int
    created_at: datetime
    messages: List[Message] = []
    
    class Config:
        from_attributes = True
