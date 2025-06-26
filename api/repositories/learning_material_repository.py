"""
Repository for managing learning materials like key concepts, flashcards, and quizzes.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from sqlalchemy.orm import Session

from models import Flashcard as FlashcardORM, KeyConcept as KeyConceptORM, QuizQuestion as QuizQuestionORM
from repositories.domain_models import KeyConcept, Flashcard, QuizQuestion
from repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class LearningMaterialRepository(BaseRepository):
    """Repository for learning material operations."""
    
    def __repr__(self):
        return f"<LearningMaterialRepository({self.database_url})>"
        
    # --- Key Concept Methods ---
    
    def add_key_concept(
        self,
        file_id: int,
        concept_title: str,
        concept_explanation: str,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Add a new key concept associated with a file and return its ID."""
        with self.get_unit_of_work() as uow:
            try:
                new_concept = KeyConceptORM(
                    file_id=file_id,
                    concept_title=concept_title,
                    concept_explanation=concept_explanation,
                    source_page_number=source_page_number,
                    source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
                    source_video_timestamp_end_seconds=source_video_timestamp_end_seconds,
                )
                uow.session.add(new_concept)
                uow.session.flush()
                new_concept_id = new_concept.id
                uow.session.commit()
                logger.info(f"Successfully added key concept for file {file_id}, id={new_concept_id}")
                return new_concept_id
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding key concept: {e}", exc_info=True)
                return None

    def get_key_concept_by_id(self, key_concept_id: int) -> Optional[KeyConceptORM]:
        """Get a single key concept by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == key_concept_id).first()

    def get_key_concepts_for_file(self, file_id: int) -> List[KeyConcept]:
        """Get key concepts for a file."""
        with self.get_unit_of_work() as uow:
            try:
                concepts_orm = uow.session.query(KeyConceptORM).filter(KeyConceptORM.file_id == file_id).all()
                return [KeyConcept.from_orm(concept) for concept in concepts_orm]
            except Exception as e:
                logger.error(f"ORM query for key concepts failed: {e}", exc_info=True)
                return []

    def update_key_concept(self, key_concept_id: int, update_data: Dict[str, Any]) -> Optional[KeyConceptORM]:
        """Update a key concept."""
        with self.get_unit_of_work() as uow:
            try:
                concept_orm = uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == key_concept_id).first()
                if not concept_orm:
                    return None
                for key, value in update_data.items():
                    setattr(concept_orm, key, value)
                uow.session.commit()
                uow.session.refresh(concept_orm)
                return concept_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating key concept {key_concept_id}: {e}", exc_info=True)
                return None

    def delete_key_concept(self, key_concept_id: int) -> bool:
        """Delete a key concept by its ID."""
        with self.get_unit_of_work() as uow:
            try:
                concept_orm = uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == key_concept_id).first()
                if not concept_orm:
                    return False
                uow.session.delete(concept_orm)
                uow.session.commit()
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting key concept {key_concept_id}: {e}", exc_info=True)
                return False

    # --- Flashcard Methods ---

    def add_flashcard(self, file_id: int, question: str, answer: str, key_concept_id: Optional[int] = None, is_custom: bool = False) -> Optional[int]:
        """Add a new flashcard and return its ID."""
        with self.get_unit_of_work() as uow:
            try:
                new_flashcard = FlashcardORM(
                    file_id=file_id,
                    key_concept_id=key_concept_id,
                    question=question,
                    answer=answer,
                    is_custom=is_custom
                )
                uow.session.add(new_flashcard)
                uow.session.flush()
                new_flashcard_id = new_flashcard.id
                uow.session.commit()
                logger.info(f"Added flashcard for file {file_id}, id={new_flashcard_id}")
                return new_flashcard_id
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding flashcard: {e}", exc_info=True)
                return None
    
    def get_flashcards_for_file(self, file_id: int) -> List[Flashcard]:
        """Get flashcards for a file by ID."""
        with self.get_unit_of_work() as uow:
            try:
                flashcards_orm = uow.session.query(FlashcardORM).filter(FlashcardORM.file_id == file_id).all()
                return [Flashcard.from_rm(fc) for fc in flashcards_orm]
            except Exception as e:
                logger.error(f"ORM query for flashcards failed: {e}", exc_info=True)
                return []
            
    def get_flashcard_by_id(self, flashcard_id: int) -> Optional[FlashcardORM]:
        """Get a single flashcard by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(FlashcardORM).filter(FlashcardORM.id == flashcard_id).first()

    def update_flashcard(self, flashcard_id: int, update_data: Dict[str, Any]) -> Optional[FlashcardORM]:
        """Update a flashcard."""
        with self.get_unit_of_work() as uow:
            try:
                flashcard_orm = uow.session.query(FlashcardORM).filter(FlashcardORM.id == flashcard_id).first()
                if not flashcard_orm:
                    return None
                for key, value in update_data.items():
                    setattr(flashcard_orm, key, value)
                uow.session.commit()
                uow.session.refresh(flashcard_orm)
                return flashcard_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating flashcard {flashcard_id}: {e}", exc_info=True)
                return None

    def delete_flashcard(self, flashcard_id: int) -> bool:
        """Delete a flashcard by its ID."""
        with self.get_unit_of_work() as uow:
            try:
                flashcard_orm = uow.session.query(FlashcardORM).filter(FlashcardORM.id == flashcard_id).first()
                if not flashcard_orm:
                    return False
                uow.session.delete(flashcard_orm)
                uow.session.commit()
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting flashcard {flashcard_id}: {e}", exc_info=True)
                return False
            
    # --- Quiz Question Methods ---

    def add_quiz_question(
        self, 
        file_id: int,
        question: str,
        question_type: str,
        correct_answer: str,
        distractors: List[str],
        key_concept_id: Optional[int] = None,
        is_custom: bool = False
    ) -> Optional[int]:
        """Add a new quiz question and return its ID."""
        with self.get_unit_of_work() as uow:
            try:
                new_quiz = QuizQuestionORM(
                    file_id=file_id,
                    key_concept_id=key_concept_id,
                    question=question,
                    question_type=question_type,
                    correct_answer=correct_answer,
                    distractors=distractors,
                    is_custom=is_custom
                )
                uow.session.add(new_quiz)
                uow.session.flush()
                new_quiz_id = new_quiz.id
                uow.session.commit()
                logger.info(f"Added quiz question for file {file_id}, id={new_quiz_id}")
                return new_quiz_id
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None

    def get_quiz_questions_for_file(self, file_id: int) -> List[QuizQuestion]:
        """Get quiz questions for a file by ID."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_questions_orm = uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.file_id == file_id).all()
                return [QuizQuestion.from_orm(qq) for qq in quiz_questions_orm]
            except Exception as e:
                logger.error(f"ORM query for quiz questions failed: {e}", exc_info=True)
                return []

    def get_quiz_question_by_id(self, quiz_question_id: int) -> Optional[QuizQuestionORM]:
        """Get a single quiz question by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.id == quiz_question_id).first()

    def update_quiz_question(self, quiz_question_id: int, update_data: Dict[str, Any]) -> Optional[QuizQuestionORM]:
        """Update a quiz question."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_question_orm = uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.id == quiz_question_id).first()
                if not quiz_question_orm:
                    return None
                for key, value in update_data.items():
                    setattr(quiz_question_orm, key, value)
                uow.session.commit()
                uow.session.refresh(quiz_question_orm)
                return quiz_question_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating quiz question {quiz_question_id}: {e}", exc_info=True)
                return None

    def delete_quiz_question(self, quiz_question_id: int) -> bool:
        """Delete a quiz question by its ID."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_question_orm = uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.id == quiz_question_id).first()
                if not quiz_question_orm:
                    return False
                uow.session.delete(quiz_question_orm)
                uow.session.commit()
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting quiz question {quiz_question_id}: {e}", exc_info=True)
                return False