from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class FlashcardBase(BaseModel):
    """Base schema for Flashcard."""
    file_id: int
    key_concept_id: Optional[int] = None
    question: str
    answer: str
    is_custom: bool = False

class FlashcardCreate(FlashcardBase):
    """Schema for creating a new Flashcard."""
    pass

class FlashcardUpdate(BaseModel):
    """Schema for updating a Flashcard."""
    question: Optional[str] = None
    answer: Optional[str] = None
    is_custom: Optional[bool] = None

class FlashcardInDBBase(FlashcardBase):
    """Base schema for Flashcard in database."""
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class Flashcard(FlashcardInDBBase):
    """Schema for Flashcard."""
    pass

class FlashcardInDB(FlashcardInDBBase):
    """Schema for Flashcard in database."""
    pass
