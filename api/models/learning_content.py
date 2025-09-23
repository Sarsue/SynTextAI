"""
Learning content related Pydantic models/schemas.
"""
from typing import List, Optional, Dict, Any, TypeVar, Generic, Type
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from enum import Enum

T = TypeVar('T')

class StandardResponse(BaseModel, Generic[T]):
    """Standard response model for API responses with generic data type."""
    success: bool
    message: str
    data: Optional[T] = None
    
    model_config = ConfigDict(from_attributes=True)

class DifficultyLevel(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class KeyConceptBase(BaseModel):
    """Base key concept model with common fields."""
    concept: str
    explanation: str
    source_page: Optional[int] = None
    source_text: Optional[str] = None
    importance: float = Field(ge=0.0, le=1.0)
    tags: List[str] = []

class KeyConceptCreate(KeyConceptBase):
    """Schema for creating a new key concept."""
    pass

class KeyConceptUpdate(BaseModel):
    """Schema for updating a key concept."""
    concept: Optional[str] = None
    explanation: Optional[str] = None
    source_page: Optional[int] = None
    source_text: Optional[str] = None
    importance: Optional[float] = Field(None, ge=0.0, le=1.0)
    tags: Optional[List[str]] = None

class KeyConceptResponse(KeyConceptBase):
    """Key concept model for API responses."""
    id: int
    file_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class FlashcardBase(BaseModel):
    """Base flashcard model with common fields."""
    front: str
    back: str
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    tags: List[str] = []
    due_date: Optional[datetime] = None

class FlashcardCreate(FlashcardBase):
    """Schema for creating a new flashcard."""
    pass

class FlashcardUpdateRequest(BaseModel):
    """Schema for updating a flashcard."""
    front: Optional[str] = None
    back: Optional[str] = None
    difficulty: Optional[DifficultyLevel] = None
    tags: Optional[List[str]] = None
    due_date: Optional[datetime] = None
    last_reviewed: Optional[datetime] = None
    ease_factor: Optional[float] = Field(None, ge=1.3, le=2.5)
    interval: Optional[int] = Field(None, ge=1)

class FlashcardResponse(FlashcardBase):
    """Flashcard model for API responses."""
    id: int
    file_id: int
    created_at: datetime
    updated_at: datetime
    last_reviewed: Optional[datetime] = None
    ease_factor: float = 2.5
    interval: int = 1
    repetitions: int = 0
    
    class Config:
        from_attributes = True

class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response model."""
    items: List[T]
    total: int
    page: int
    page_size: int
    
    model_config = ConfigDict(from_attributes=True)

class FlashcardsListResponse(PaginatedResponse[FlashcardResponse]):
    """Response model for listing flashcards with pagination."""
    pass

class QuizQuestionBase(BaseModel):
    """Base quiz question model with common fields."""
    question: str
    options: List[str]
    correct_answer: int
    explanation: str
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    tags: List[str] = []
    source_page: Optional[int] = None
    source_text: Optional[str] = None

class QuizQuestionCreate(QuizQuestionBase):
    """Schema for creating a new quiz question."""
    pass

class QuizQuestionUpdate(BaseModel):
    """Schema for updating a quiz question."""
    question: Optional[str] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[int] = None
    explanation: Optional[str] = None
    difficulty: Optional[DifficultyLevel] = None
    tags: Optional[List[str]] = None
    source_page: Optional[int] = None
    source_text: Optional[str] = None

class QuizQuestionResponse(QuizQuestionBase):
    """Quiz question model for API responses."""
    id: int
    file_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class QuizQuestionsListResponse(PaginatedResponse[QuizQuestionResponse]):
    """Response model for listing quiz questions with pagination."""
    pass
