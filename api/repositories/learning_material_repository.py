"""
Repository for managing learning materials like key concepts, flashcards, and quizzes.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from sqlalchemy.orm import Session, selectinload

from ..models import File, Flashcard as FlashcardORM, KeyConcept as KeyConceptORM, QuizQuestion as QuizQuestionORM
from .domain_models import KeyConcept, Flashcard, QuizQuestion
from ..schemas.learning_content import (
    KeyConceptCreate, KeyConceptUpdate, KeyConceptResponse,
    FlashcardCreate, FlashcardUpdate, FlashcardResponse,
    QuizQuestionCreate, QuizQuestionUpdate, QuizQuestionResponse
)
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class LearningMaterialRepository(BaseRepository):
    """Repository for learning material operations."""
    
    def __repr__(self):
        return f"<LearningMaterialRepository({self.database_url})>"
        
    # --- Key Concept Methods ---
    
    def add_key_concept(self, file_id: int, key_concept_data: KeyConceptCreate) -> Optional[KeyConceptORM]:
        """Add a new key concept from a Pydantic model and return the ORM instance."""
        with self.get_unit_of_work() as uow:
            try:
                new_concept = KeyConceptORM(
                    file_id=file_id,
                    concept_title=key_concept_data.concept,  # Map from schema to ORM
                    concept_explanation=key_concept_data.explanation,  # Map from schema to ORM
                    source_link=key_concept_data.source_link,
                    is_custom=key_concept_data.is_custom
                )
                uow.session.add(new_concept)
                uow.session.commit()
                uow.session.refresh(new_concept)
                logger.info(f"Successfully added key concept for file {file_id}, id={new_concept.id}")
                return new_concept
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding key concept: {e}", exc_info=True)
                return None

    def get_key_concept_by_id(self, key_concept_id: int) -> Optional[KeyConceptORM]:
        """Get a single key concept by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == key_concept_id).first()

    def get_key_concepts_for_file(self, file_id: int) -> List[KeyConceptResponse]:
        """Get key concepts for a file only if it has been processed."""
        with self.get_unit_of_work() as uow:
            try:
                key_concepts_orm = uow.session.query(KeyConceptORM).join(File).filter(
                    KeyConceptORM.file_id == file_id,
                    File.processing_status == 'processed'
                ).all()
                return [KeyConceptResponse.from_orm(kc) for kc in key_concepts_orm]
            except Exception as e:
                logger.error(f"ORM query for key concepts failed: {e}", exc_info=True)
                return []

    def update_key_concept(self, key_concept_id: int, user_id: int, update_data: KeyConceptUpdate) -> Optional[KeyConceptORM]:
        """Update a key concept's details from a Pydantic model, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                concept_orm = uow.session.query(KeyConceptORM).join(KeyConceptORM.file).filter(
                    KeyConceptORM.id == key_concept_id,
                    File.user_id == user_id
                ).first()

                if not concept_orm:
                    logger.warning(f"Update failed: KeyConcept {key_concept_id} not found or user {user_id} lacks ownership.")
                    return None

                update_dict = update_data.dict(exclude_unset=True)
                # Map schema fields to ORM fields safely
                field_map = {
                    'concept': 'concept_title',
                    'explanation': 'concept_explanation',
                    'source_link': 'source_link'
                }
                for schema_field, orm_field in field_map.items():
                    if schema_field in update_dict:
                        setattr(concept_orm, orm_field, update_dict[schema_field])

                uow.session.commit()
                uow.session.refresh(concept_orm)
                logger.info(f"Successfully updated KeyConcept {key_concept_id} by user {user_id}.")
                return concept_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating key concept {key_concept_id}: {e}", exc_info=True)
                return None

    def delete_key_concept(self, key_concept_id: int, user_id: int) -> bool:
        """Delete a key concept by its ID, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                # Join with File to check ownership
                concept_orm = uow.session.query(KeyConceptORM).join(KeyConceptORM.file).filter(
                    KeyConceptORM.id == key_concept_id,
                    File.user_id == user_id
                ).first()

                if not concept_orm:
                    logger.warning(f"Delete failed: KeyConcept {key_concept_id} not found or user {user_id} lacks ownership.")
                    return False

                uow.session.delete(concept_orm)
                uow.session.commit()
                logger.info(f"Successfully deleted KeyConcept {key_concept_id} by user {user_id}.")
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting key concept {key_concept_id}: {e}", exc_info=True)
                return False

    # --- Flashcard Methods ---

    def add_flashcard(self, file_id: int, flashcard_data: FlashcardCreate) -> Optional[FlashcardORM]:
        """Add a new flashcard from a Pydantic model and return the ORM instance."""
        with self.get_unit_of_work() as uow:
            try:
                new_flashcard = FlashcardORM(
                    file_id=file_id,
                    question=flashcard_data.question,
                    answer=flashcard_data.answer,
                    key_concept_id=flashcard_data.key_concept_id,
                    is_custom=flashcard_data.is_custom
                )
                uow.session.add(new_flashcard)
                uow.session.commit()
                uow.session.refresh(new_flashcard)
                logger.info(f"Successfully added flashcard for file {file_id}, id={new_flashcard.id}")
                return new_flashcard
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding flashcard: {e}", exc_info=True)
                return None
    
    def get_flashcards_for_file(self, file_id: int) -> List[FlashcardResponse]:
        """Get flashcards for a file by ID, only if it has been processed."""
        with self.get_unit_of_work() as uow:
            try:
                flashcards_orm = uow.session.query(FlashcardORM).join(File).filter(
                    FlashcardORM.file_id == file_id,
                    File.processing_status == 'processed'
                ).all()
                return [FlashcardResponse.from_orm(f) for f in flashcards_orm]
            except Exception as e:
                logger.error(f"ORM query for flashcards failed: {e}", exc_info=True)
                return []
            
    def get_flashcard_by_id(self, flashcard_id: int) -> Optional[FlashcardORM]:
        """Get a single flashcard by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(FlashcardORM).options(selectinload('*')).filter(FlashcardORM.id == flashcard_id).first()

    def update_flashcard(self, flashcard_id: int, user_id: int, update_data: FlashcardUpdate) -> Optional[FlashcardORM]:
        """Update a flashcard's details from a Pydantic model, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                flashcard_orm = uow.session.query(FlashcardORM).join(FlashcardORM.file).filter(
                    FlashcardORM.id == flashcard_id,
                    File.user_id == user_id
                ).first()

                if not flashcard_orm:
                    logger.warning(f"Update failed: Flashcard {flashcard_id} not found or user {user_id} lacks ownership.")
                    return None

                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    setattr(flashcard_orm, key, value)

                uow.session.commit()
                uow.session.refresh(flashcard_orm)
                logger.info(f"Successfully updated Flashcard {flashcard_id} by user {user_id}.")
                return flashcard_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating flashcard {flashcard_id}: {e}", exc_info=True)
                return None

    def delete_flashcard(self, flashcard_id: int, user_id: int) -> bool:
        """Delete a flashcard by its ID, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                flashcard_orm = uow.session.query(FlashcardORM).join(FlashcardORM.file).filter(
                    FlashcardORM.id == flashcard_id,
                    File.user_id == user_id
                ).first()

                if not flashcard_orm:
                    logger.warning(f"Delete failed: Flashcard {flashcard_id} not found or user {user_id} lacks ownership.")
                    return False

                uow.session.delete(flashcard_orm)
                uow.session.commit()
                logger.info(f"Successfully deleted Flashcard {flashcard_id} by user {user_id}.")
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting flashcard {flashcard_id}: {e}", exc_info=True)
                return False
            
    # --- Quiz Question Methods ---

    def add_quiz_question(self, file_id: int, quiz_question_data: QuizQuestionCreate) -> Optional[QuizQuestionORM]:
        """Add a new quiz question from a Pydantic model and return the ORM instance."""
        with self.get_unit_of_work() as uow:
            try:
                new_quiz = QuizQuestionORM(
                    file_id=file_id,
                    key_concept_id=quiz_question_data.key_concept_id,
                    question=quiz_question_data.question,
                    question_type=quiz_question_data.question_type,
                    correct_answer=quiz_question_data.correct_answer,
                    distractors=quiz_question_data.distractors or [],
                    explanation=quiz_question_data.explanation,
                    difficulty=quiz_question_data.difficulty,
                    is_custom=quiz_question_data.is_custom
                )
                uow.session.add(new_quiz)
                uow.session.commit()
                uow.session.refresh(new_quiz)
                logger.info(f"Added quiz question for file {file_id}, id={new_quiz.id}")
                return new_quiz
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None

    def get_quiz_questions_for_file(self, file_id: int) -> List[QuizQuestionResponse]:
        """Get quiz questions for a file by ID, only if it has been processed."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_questions_orm = uow.session.query(QuizQuestionORM).join(File).filter(
                    QuizQuestionORM.file_id == file_id,
                    File.processing_status == 'processed'
                ).all()
                return [QuizQuestionResponse.from_orm(q) for q in quiz_questions_orm]
            except Exception as e:
                logger.error(f"ORM query for quiz questions failed: {e}", exc_info=True)
                return []

    def get_quiz_question_by_id(self, quiz_question_id: int) -> Optional[QuizQuestionORM]:
        """Get a single quiz question by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.id == quiz_question_id).first()

    def update_quiz_question(self, quiz_question_id: int, user_id: int, update_data: QuizQuestionUpdate) -> Optional[QuizQuestionORM]:
        """Update a quiz question's details from a Pydantic model, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_question_orm = uow.session.query(QuizQuestionORM).join(QuizQuestionORM.file).filter(
                    QuizQuestionORM.id == quiz_question_id,
                    File.user_id == user_id
                ).first()

                if not quiz_question_orm:
                    logger.warning(f"Update failed: QuizQuestion {quiz_question_id} not found or user {user_id} lacks ownership.")
                    return None

                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    if hasattr(quiz_question_orm, key):
                        setattr(quiz_question_orm, key, value)

                uow.session.commit()
                uow.session.refresh(quiz_question_orm)
                logger.info(f"Successfully updated QuizQuestion {quiz_question_id} by user {user_id}.")
                return quiz_question_orm
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating quiz question {quiz_question_id}: {e}", exc_info=True)
                return None

    def delete_quiz_question(self, quiz_question_id: int, user_id: int) -> bool:
        """Delete a quiz question by its ID, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_question_orm = uow.session.query(QuizQuestionORM).join(QuizQuestionORM.file).filter(
                    QuizQuestionORM.id == quiz_question_id,
                    File.user_id == user_id
                ).first()

                if not quiz_question_orm:
                    logger.warning(f"Delete failed: QuizQuestion {quiz_question_id} not found or user {user_id} lacks ownership.")
                    return False

                uow.session.delete(quiz_question_orm)
                uow.session.commit()
                logger.info(f"Successfully deleted QuizQuestion {quiz_question_id} by user {user_id}.")
                return True
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting quiz question {quiz_question_id}: {e}", exc_info=True)
                return False