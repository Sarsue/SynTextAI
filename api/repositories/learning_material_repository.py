"""
Repository for managing learning materials like key concepts, flashcards, and quizzes.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from sqlalchemy.orm import Session, selectinload

from models import File, Flashcard as FlashcardORM, KeyConcept as KeyConceptORM, QuizQuestion as QuizQuestionORM
from repositories.domain_models import KeyConcept, Flashcard, QuizQuestion
from schemas.learning_content import KeyConceptResponse, FlashcardResponse, QuizQuestionResponse
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

    def get_key_concepts_for_file(self, file_id: int) -> List[KeyConceptResponse]:
        """Get key concepts for a file."""
        with self.get_unit_of_work() as uow:
            try:
                key_concepts_orm = uow.session.query(KeyConceptORM).filter(KeyConceptORM.file_id == file_id).all()
                return [KeyConceptResponse.from_orm(kc) for kc in key_concepts_orm]
            except Exception as e:
                logger.error(f"ORM query for key concepts failed: {e}", exc_info=True)
                return []

    def update_key_concept(self, key_concept_id: int, user_id: int, update_data: KeyConcept) -> Optional[KeyConceptORM]:
        """Update a key concept's details, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                concept_orm = uow.session.query(KeyConceptORM).join(KeyConceptORM.file).filter(
                    KeyConceptORM.id == key_concept_id,
                    File.user_id == user_id
                ).first()

                if not concept_orm:
                    logger.warning(f"Update failed: KeyConcept {key_concept_id} not found or user {user_id} lacks ownership.")
                    return None

                # Update fields from the domain model
                concept_orm.concept_title = update_data.concept_title
                concept_orm.concept_explanation = update_data.concept_explanation
                concept_orm.source_page_number = update_data.source_page_number
                concept_orm.source_video_timestamp_start_seconds = update_data.source_video_timestamp_start_seconds
                concept_orm.source_video_timestamp_end_seconds = update_data.source_video_timestamp_end_seconds

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
    
    def get_flashcards_for_file(self, file_id: int) -> List[FlashcardResponse]:
        """Get flashcards for a file by ID."""
        with self.get_unit_of_work() as uow:
            try:
                flashcards_orm = uow.session.query(FlashcardORM).filter(FlashcardORM.file_id == file_id).all()
                return [FlashcardResponse.from_orm(fc) for fc in flashcards_orm]
            except Exception as e:
                logger.error(f"ORM query for flashcards failed: {e}", exc_info=True)
                return []
            
    def get_flashcard_by_id(self, flashcard_id: int) -> Optional[FlashcardORM]:
        """Get a single flashcard by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(FlashcardORM).options(selectinload('*')).filter(FlashcardORM.id == flashcard_id).first()

    def update_flashcard(self, flashcard_id: int, user_id: int, update_data: Flashcard) -> Optional[FlashcardORM]:
        """Update a flashcard's details, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                flashcard_orm = uow.session.query(FlashcardORM).join(FlashcardORM.file).filter(
                    FlashcardORM.id == flashcard_id,
                    File.user_id == user_id
                ).first()

                if not flashcard_orm:
                    logger.warning(f"Update failed: Flashcard {flashcard_id} not found or user {user_id} lacks ownership.")
                    return None

                flashcard_orm.question = update_data.question
                flashcard_orm.answer = update_data.answer

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

    def get_quiz_questions_for_file(self, file_id: int) -> List[QuizQuestionResponse]:
        """Get quiz questions for a file by ID."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_questions_orm = uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.file_id == file_id).all()
                return [QuizQuestionResponse.from_orm(q) for q in quiz_questions_orm]
            except Exception as e:
                logger.error(f"ORM query for quiz questions failed: {e}", exc_info=True)
                return []

    def get_quiz_question_by_id(self, quiz_question_id: int) -> Optional[QuizQuestionORM]:
        """Get a single quiz question by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(QuizQuestionORM).filter(QuizQuestionORM.id == quiz_question_id).first()

    def update_quiz_question(self, quiz_question_id: int, user_id: int, update_data: QuizQuestion) -> Optional[QuizQuestionORM]:
        """Update a quiz question's details, ensuring user ownership."""
        with self.get_unit_of_work() as uow:
            try:
                quiz_question_orm = uow.session.query(QuizQuestionORM).join(QuizQuestionORM.file).filter(
                    QuizQuestionORM.id == quiz_question_id,
                    File.user_id == user_id
                ).first()

                if not quiz_question_orm:
                    logger.warning(f"Update failed: QuizQuestion {quiz_question_id} not found or user {user_id} lacks ownership.")
                    return None

                quiz_question_orm.question_text = update_data.question_text
                quiz_question_orm.options = update_data.options
                quiz_question_orm.correct_answer = update_data.correct_answer
                quiz_question_orm.explanation = update_data.explanation

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