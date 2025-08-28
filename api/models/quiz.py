"""
Pydantic models for Quiz Question operations.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class QuizQuestionBase(BaseModel):
    """Base schema for Quiz Question."""
    file_id: int
    key_concept_id: Optional[int] = None
    question: str
    question_type: str
    correct_answer: str
    distractors: Optional[List[Dict[str, Any]]] = None
    is_custom: bool = False

class QuizQuestionCreate(QuizQuestionBase):
    """Schema for creating a new Quiz Question."""
    pass

class QuizQuestionUpdate(BaseModel):
    """Schema for updating a Quiz Question."""
    question: Optional[str] = None
    question_type: Optional[str] = None
    correct_answer: Optional[str] = None
    distractors: Optional[List[Dict[str, Any]]] = None
    is_custom: Optional[bool] = None

class QuizQuestionInDBBase(QuizQuestionBase):
    """Base schema for Quiz Question in database."""
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class QuizQuestion(QuizQuestionInDBBase):
    """Schema for Quiz Question."""
    pass

class QuizQuestionInDB(QuizQuestionInDBBase):
    """Schema for Quiz Question in database."""
    pass
