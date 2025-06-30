import json
import logging
from datetime import datetime
from typing import List, Optional, TypeVar, Generic

from pydantic import BaseModel, Field, field_validator

T = TypeVar('T')

class StandardResponse(BaseModel, Generic[T]):
    status: str = "success"
    message: Optional[str] = None
    data: Optional[T] = None


logger = logging.getLogger(__name__)

# --- Key Concepts ---

class KeyConceptCreate(BaseModel):
    concept: str
    explanation: str
    source_link: Optional[str] = None
    is_custom: bool = True

class KeyConceptUpdate(BaseModel):
    concept: Optional[str] = None
    explanation: Optional[str] = None
    source_link: Optional[str] = None

class KeyConceptResponse(BaseModel):
    id: int
    file_id: int
    concept: str = Field(alias='concept_title')
    explanation: str = Field(alias='concept_explanation')
    source_link: Optional[str] = None
    is_custom: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True

class KeyConceptsListResponse(BaseModel):
    key_concepts: List[KeyConceptResponse]

# --- Flashcards ---

class FlashcardCreate(BaseModel):
    question: str
    answer: str
    key_concept_id: Optional[int] = None
    is_custom: bool = True

class FlashcardUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    key_concept_id: Optional[int] = None

class FlashcardResponse(BaseModel):
    id: int
    file_id: int
    question: str
    answer: str
    key_concept_id: Optional[int] = None
    is_custom: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FlashcardsListResponse(BaseModel):
    flashcards: List[FlashcardResponse]

# --- Quiz Questions ---

class QuizQuestionCreate(BaseModel):
    question: str
    correct_answer: str
    distractors: List[str] = []
    key_concept_id: Optional[int] = None
    question_type: str = "MCQ"
    explanation: Optional[str] = ""
    difficulty: str = "medium"
    is_custom: bool = True

class QuizQuestionUpdate(BaseModel):
    question: Optional[str] = None
    correct_answer: Optional[str] = None
    distractors: Optional[List[str]] = None
    key_concept_id: Optional[int] = None
    question_type: Optional[str] = None
    explanation: Optional[str] = None
    difficulty: Optional[str] = None

class QuizQuestionResponse(BaseModel):
    id: int
    file_id: int
    key_concept_id: Optional[int] = None
    question: str
    question_type: str = "MCQ"
    correct_answer: str = ""
    distractors: List[str] = []
    answer_explanation: Optional[str] = Field(default="", alias="explanation")
    difficulty: Optional[str] = "medium"
    is_custom: bool = False

    class Config:
        from_attributes = True
        populate_by_name = True

    @field_validator('distractors', mode='before')
    def parse_distractors_from_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse distractors string to JSON: {v}")
                return []
        return v

class QuizQuestionsListResponse(BaseModel):
    quizzes: List[QuizQuestionResponse]


# --- Request-specific Update Models ---
# These are aliases for the main update models, used for clarity in the API routes.
KeyConceptUpdateRequest = KeyConceptUpdate
FlashcardUpdateRequest = FlashcardUpdate
QuizQuestionUpdateRequest = QuizQuestionUpdate
