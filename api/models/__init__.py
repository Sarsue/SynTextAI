"""
SQLAlchemy ORM models, Pydantic schemas, and database utilities for the application.

This module provides:
- Database models (SQLAlchemy ORM)
- Pydantic schemas for request/response validation
- Database connection and session management
- Common database operations and utilities
"""

# Database utilities
from .db_utils import (
    get_engine,
    get_session_factory,
    get_async_session,
    init_db,
    close_db,
    Base,
)

# ORM Models
from .orm_models import (
    User,
    Subscription,
    CardDetails,
    File,
    Segment,
    Chunk,
    KeyConcept,
    ChatHistory,
    Message,
    Flashcard,
    QuizQuestion
)

# Pydantic Schemas
# Temporarily commenting out missing schema imports for basic functionality
# from .user import UserCreate, UserUpdate, UserInDB, User as UserSchema
# from .chat import (
#     MessageBase, MessageCreate, MessageUpdate, Message,
#     ChatHistoryBase, ChatHistoryCreate, ChatHistoryUpdate, ChatHistory as ChatHistorySchema
# )
# from .file import FileCreate, FileUpdate, FileInDB, File as FileSchema
# from .flashcard import (
#     FlashcardBase, FlashcardCreate, FlashcardUpdate,
#     FlashcardInDB, Flashcard as FlashcardSchema
# )
# from .key_concept import (
#     KeyConceptBase, KeyConceptCreate, KeyConceptUpdate,
#     KeyConceptInDB, KeyConcept as KeyConceptSchema
# )
# from .quiz import (
#     QuizQuestionBase, QuizQuestionCreate, QuizQuestionUpdate,
#     QuizQuestionInDB, QuizQuestion as QuizQuestionSchema
# )

__all__ = [
    # Database utilities
    'get_engine',
    'get_session_factory',
    'get_async_session',
    'init_db',
    'close_db',
    'Base',
    
    # ORM Models
    'User',
    'Subscription',
    'CardDetails',
    'File',
    'Segment',
    'Chunk',
    'KeyConcept',
    'ChatHistory',
    'Message',
    'Flashcard',
    'QuizQuestion',
    
    # Pydantic Schemas
    # Temporarily commenting out missing schema exports for basic functionality
    # User
    # 'UserCreate', 'UserUpdate', 'UserInDB', 'UserSchema',
    
    # Chat
    # 'MessageBase', 'MessageCreate', 'MessageUpdate', 'Message',
    # 'ChatHistoryBase', 'ChatHistoryCreate', 'ChatHistoryUpdate', 'ChatHistorySchema',
    
    # File
    # 'FileCreate', 'FileUpdate', 'FileInDB', 'FileSchema',
    
    # Flashcard
    # 'FlashcardBase', 'FlashcardCreate', 'FlashcardUpdate',
    # 'FlashcardInDB', 'FlashcardSchema',
    
    # Key Concept
    # 'KeyConceptBase', 'KeyConceptCreate', 'KeyConceptUpdate',
    # 'KeyConceptInDB', 'KeyConceptSchema',
    
    # Quiz
    # 'QuizQuestionBase', 'QuizQuestionCreate', 'QuizQuestionUpdate',
    # 'QuizQuestionInDB', 'QuizQuestionSchema'
]
