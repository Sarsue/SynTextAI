from pydantic import BaseModel
from typing import Optional, List

class KeyConceptUpdate(BaseModel):
    concept_title: Optional[str] = None
    concept_explanation: Optional[str] = None

class FlashcardUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None

class QuizQuestionUpdate(BaseModel):
    question: Optional[str] = None
    correct_answer: Optional[str] = None
    distractors: Optional[List[str]] = None
