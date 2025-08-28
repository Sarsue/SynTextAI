"""
SQLAlchemy ORM models and Pydantic schemas for the application.
"""

# ORM Models
from .orm_models import (
    Base,
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
from .user import UserCreate, UserUpdate, UserInDB, User as UserSchema
from .chat import (
    MessageBase, MessageCreate, MessageUpdate, Message,
    ChatHistoryBase, ChatHistoryCreate, ChatHistoryUpdate, ChatHistory as ChatHistorySchema
)
from .file import FileCreate, FileUpdate, FileInDB, File as FileSchema
from .flashcard import (
    FlashcardBase, FlashcardCreate, FlashcardUpdate,
    FlashcardInDB, Flashcard as FlashcardSchema
)
from .key_concept import (
    KeyConceptBase, KeyConceptCreate, KeyConceptUpdate,
    KeyConceptInDB, KeyConcept as KeyConceptSchema
)
from .quiz import (
    QuizQuestionBase, QuizQuestionCreate, QuizQuestionUpdate,
    QuizQuestionInDB, QuizQuestion as QuizQuestionSchema
)

__all__ = [
    # ORM Models
    'Base',
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
    # User
    'UserCreate', 'UserUpdate', 'UserInDB', 'UserSchema',
    
    # Chat
    'MessageBase', 'MessageCreate', 'MessageUpdate', 'Message',
    'ChatHistoryBase', 'ChatHistoryCreate', 'ChatHistoryUpdate', 'ChatHistorySchema',
    
    # File
    'FileCreate', 'FileUpdate', 'FileInDB', 'FileSchema',
    
    # Flashcard
    'FlashcardBase', 'FlashcardCreate', 'FlashcardUpdate',
    'FlashcardInDB', 'FlashcardSchema',
    
    # Key Concept
    'KeyConceptBase', 'KeyConceptCreate', 'KeyConceptUpdate',
    'KeyConceptInDB', 'KeyConceptSchema',
    
    # Quiz
    'QuizQuestionBase', 'QuizQuestionCreate', 'QuizQuestionUpdate',
    'QuizQuestionInDB', 'QuizQuestionSchema'
]
