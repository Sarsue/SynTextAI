import json
import logging
from datetime import datetime
from typing import List, Optional, TypeVar, Generic

from pydantic import BaseModel, Field, field_validator, ConfigDict, model_validator

T = TypeVar('T')

class StandardResponse(BaseModel, Generic[T]):
    status: str = "success"
    message: Optional[str] = None
    data: Optional[T] = None


logger = logging.getLogger(__name__)

# --- Key Concepts ---

class KeyConceptCreate(BaseModel):
    # Accept both new and old field names for backward compatibility
    concept_title: Optional[str] = None
    concept: Optional[str] = None  # Old field name for backward compatibility
    concept_explanation: Optional[str] = None
    explanation: Optional[str] = None  # Old field name for backward compatibility
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None
    is_custom: bool = False

    # Custom validator to handle both old and new field names
    model_config = ConfigDict(
        extra='forbid',
        populate_by_name=True
    )
    
    @model_validator(mode='after')
    def check_fields(self) -> 'KeyConceptCreate':
        if self.concept_title is None and self.concept is not None:
            self.concept_title = self.concept
        if self.concept_explanation is None and self.explanation is not None:
            self.concept_explanation = self.explanation
            
        if self.concept_title is None:
            raise ValueError("Either 'concept_title' or 'concept' must be provided")
        if self.concept_explanation is None:
            raise ValueError("Either 'concept_explanation' or 'explanation' must be provided")
            
        return self

class KeyConceptUpdate(BaseModel):
    concept_title: Optional[str] = None
    concept_explanation: Optional[str] = None
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None

class KeyConceptResponse(BaseModel):
    id: int
    concept_title: str = Field(alias='concept_title')
    concept_explanation: str = Field(alias='concept_explanation')
    # Add aliases for backward compatibility with frontend
    concept: str = Field(alias='concept_title')
    explanation: str = Field(alias='concept_explanation')
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None

    class Config:
        from_attributes = True
        populate_by_name = True

class KeyConceptsListResponse(BaseModel):
    key_concepts: List[KeyConceptResponse] = Field(alias='key_concepts')

# --- Flashcards ---

class FlashcardCreate(BaseModel):
    question: str
    answer: str
    key_concept_id: Optional[int] = None
    is_custom: bool = False

class FlashcardUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    key_concept_id: Optional[int] = None

class FlashcardResponse(BaseModel):
    id: int
    question: str
    answer: str

    class Config:
        from_attributes = True

class FlashcardsListResponse(BaseModel):
    flashcards: List[FlashcardResponse]

# --- Quiz Questions ---

class QuizQuestionCreate(BaseModel):
    question: str
    correct_answer: str
    distractors: List[str]
    key_concept_id: Optional[int] = None
    question_type: str = "MCQ"
    explanation: Optional[str] = ""
    difficulty: str = "medium"
    is_custom: bool = False

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
    question: str
    question_type: str
    correct_answer: str
    distractors: Optional[List[str]] = []

    class Config:
        from_attributes = True

    @field_validator('distractors', mode='before')
    def parse_distractors_from_json(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse distractors string to JSON: {v}")
                return []
        return v

class QuizQuestionsListResponse(BaseModel):
    quizzes: List[QuizQuestionResponse] = Field(alias='quizzes')


# --- Request-specific Update Models ---
# These are aliases for the main update models, used for clarity in the API routes.
KeyConceptUpdateRequest = KeyConceptUpdate
FlashcardUpdateRequest = FlashcardUpdate
QuizQuestionUpdateRequest = QuizQuestionUpdate
