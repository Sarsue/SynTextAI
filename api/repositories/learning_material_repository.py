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
    
    def add_key_concept(self, file_id: int, key_concept_data: KeyConceptCreate) -> Optional[dict]:
        """
        Add a new key concept from a Pydantic model and return the ORM instance.
        
        Args:
            file_id: The ID of the file to associate with the key concept
            key_concept_data: Pydantic model containing key concept data
            
        Returns:
            KeyConceptORM: The newly created key concept ORM instance, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                # Log the incoming data for debugging
                logger.debug(f"Creating new key concept for file {file_id} with data: {key_concept_data.dict()}")
                
                # Get the concept title and explanation, preferring the new field names
                concept_title = key_concept_data.concept_title or key_concept_data.concept
                concept_explanation = key_concept_data.concept_explanation or key_concept_data.explanation
                
                # Validate required fields
                if not concept_title:
                    raise ValueError("concept_title (or concept) is required")
                if not concept_explanation:
                    raise ValueError("concept_explanation (or explanation) is required")
                
                # Create the new concept with the provided data
                new_concept = KeyConceptORM(
                    file_id=file_id,
                    concept_title=concept_title,
                    concept_explanation=concept_explanation,
                    source_page_number=key_concept_data.source_page_number,
                    source_video_timestamp_start_seconds=key_concept_data.source_video_timestamp_start_seconds,
                    source_video_timestamp_end_seconds=key_concept_data.source_video_timestamp_end_seconds,
                    is_custom=key_concept_data.is_custom
                )
                
                # Add to session and commit
                uow.session.add(new_concept)
                uow.session.commit()
                
                # Explicitly refresh to ensure we have all attributes
                uow.session.refresh(new_concept)
                
                # Create a dictionary of the data we need before the session closes
                concept_data = {
                    'id': new_concept.id,
                    'file_id': new_concept.file_id,
                    'concept_title': new_concept.concept_title,
                    'concept': new_concept.concept_title,  # For backward compatibility
                    'concept_explanation': new_concept.concept_explanation,
                    'explanation': new_concept.concept_explanation,  # For backward compatibility
                    'source_page_number': new_concept.source_page_number,
                    'source_video_timestamp_start_seconds': new_concept.source_video_timestamp_start_seconds,
                    'source_video_timestamp_end_seconds': new_concept.source_video_timestamp_end_seconds,
                    'is_custom': new_concept.is_custom,
                    'created_at': new_concept.created_at,
                    'updated_at': new_concept.updated_at
                }
                
                # Log success with the ID
                logger.info(f"Successfully added key concept for file {file_id}, id={new_concept.id}")
                
                # Return the data dictionary instead of the ORM object
                return concept_data
                
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
        logger.info(f"[DEBUG] Starting get_key_concepts_for_file for file_id: {file_id}")
        with self.get_unit_of_work() as uow:
            try:
                # First, verify the file exists and is processed
                logger.info(f"[DEBUG] Checking file {file_id} status")
                file = uow.session.query(File).filter(
                    File.id == file_id,
                    File.processing_status == 'processed'
                ).first()
                
                if not file:
                    logger.warning(f"[DEBUG] File {file_id} not found or not processed")
                    return []
                
                logger.info(f"[DEBUG] File {file_id} is processed. Querying key concepts...")
                # Then get all key concepts for this file
                key_concepts_orm = uow.session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).all()
                
                logger.info(f"[DEBUG] Raw key concepts query result: {key_concepts_orm}")
                logger.info(f"[DEBUG] Found {len(key_concepts_orm)} key concepts for file {file_id}")
                
                # Convert to response models
                result = [KeyConceptResponse.from_orm(kc) for kc in key_concepts_orm]
                logger.info(f"[DEBUG] Converted to {len(result)} response models")
                return result
                
            except Exception as e:
                logger.error(f"[ERROR] ORM query for key concepts failed: {e}", exc_info=True)
                return []

    def update_key_concept(self, concept_id: int, update_data: KeyConceptUpdate) -> Optional[dict]:
        """
        Update a key concept from a Pydantic model and return the updated data as a dictionary.
        
        Args:
            concept_id: The ID of the key concept to update
            update_data: Pydantic model containing the updates
            
        Returns:
            dict: The updated key concept data, or None if the update failed
        """
        with self.get_unit_of_work() as uow:
            try:
                # Get the concept with all its relationships loaded
                concept = uow.session.query(KeyConceptORM).filter_by(id=concept_id).first()
                if not concept:
                    return None
                
                # Update the concept with the new data
                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    setattr(concept, key, value)
                
                # Save changes
                uow.session.add(concept)
                uow.session.commit()
                
                # Convert to dictionary before the session is closed
                result = {
                    'id': concept.id,
                    'file_id': concept.file_id,
                    'concept_title': concept.concept_title,
                    'concept_explanation': concept.concept_explanation,
                    'source_page_number': concept.source_page_number,
                    'source_video_timestamp_start_seconds': concept.source_video_timestamp_start_seconds,
                    'source_video_timestamp_end_seconds': concept.source_video_timestamp_end_seconds,
                    'is_custom': concept.is_custom,
                    'created_at': concept.created_at,
                    'updated_at': concept.updated_at
                }
                
                logger.info(f"Successfully updated KeyConcept {concept_id}.")
                return result
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating key concept {concept_id}: {e}", exc_info=True)
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
        """
        Add a new flashcard from a Pydantic model and return the ORM instance.
        
        Args:
            file_id: The ID of the file to associate with the flashcard
            flashcard_data: Pydantic model containing flashcard data
            
        Returns:
            FlashcardORM: The newly created flashcard ORM instance, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                logger.debug(f"Creating new flashcard for file {file_id} with data: {flashcard_data.dict()}")
                
                # Create the new flashcard instance
                new_flashcard = FlashcardORM(
                    file_id=file_id,
                    question=flashcard_data.question,
                    answer=flashcard_data.answer,
                    key_concept_id=flashcard_data.key_concept_id,
                    is_custom=flashcard_data.is_custom
                )
                
                # Add to session and commit
                uow.session.add(new_flashcard)
                uow.session.commit()
                
                # Explicitly refresh to ensure we have all attributes
                uow.session.refresh(new_flashcard)
                
                # Log success with the ID
                logger.info(f"Successfully added flashcard for file {file_id}, id={new_flashcard.id}")
                
                # Return the flashcard object - it's still attached to the session
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

    def update_flashcard(self, flashcard_id: int, user_id: int, update_data: FlashcardUpdate) -> Optional[Dict[str, Any]]:
        """Update a flashcard's details from a Pydantic model, ensuring user ownership.
        
        Returns:
            Dictionary with the updated flashcard data or None if not found
        """
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
                
                # Return a dictionary with the updated data before the session closes
                return {
                    "id": flashcard_orm.id,
                    "question": flashcard_orm.question,
                    "answer": flashcard_orm.answer,
                    "file_id": flashcard_orm.file_id,
                    "key_concept_id": flashcard_orm.key_concept_id,
                    "is_custom": flashcard_orm.is_custom,
                    "created_at": flashcard_orm.created_at,
                    "updated_at": flashcard_orm.updated_at
                }
                
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
        """
        Add a new quiz question from a Pydantic model and return the ORM instance.
        
        Args:
            file_id: The ID of the file to associate with the quiz question
            quiz_question_data: Pydantic model containing quiz question data
            
        Returns:
            QuizQuestionORM: The newly created quiz question ORM instance, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                # Log the incoming data for debugging
                logger.debug(f"Creating new quiz question for file {file_id} with data: {quiz_question_data.dict()}")
                
                # Validate required fields
                if not quiz_question_data.question:
                    raise ValueError("Question is required")
                if not quiz_question_data.correct_answer:
                    raise ValueError("Correct answer is required")
                if quiz_question_data.distractors is None:
                    quiz_question_data.distractors = []
                
                # Create the new quiz question
                new_quiz = QuizQuestionORM(
                    file_id=file_id,
                    key_concept_id=quiz_question_data.key_concept_id,
                    question=quiz_question_data.question,
                    question_type=quiz_question_data.question_type or "MCQ",
                    correct_answer=quiz_question_data.correct_answer,
                    distractors=quiz_question_data.distractors,
                    is_custom=quiz_question_data.is_custom
                )
                
                # Add to session and commit
                uow.session.add(new_quiz)
                uow.session.commit()
                
                # Explicitly refresh to ensure we have all attributes
                uow.session.refresh(new_quiz)
                
                # Log success with the ID
                logger.info(f"Successfully added quiz question for file {file_id}, id={new_quiz.id}")
                
                # Return the quiz question object - it's still attached to the session
                return new_quiz
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None

    def get_quiz_questions_for_file(self, file_id: int) -> List[QuizQuestionResponse]:
        """Get quiz questions for a file by ID, only if it has been processed."""
        logger.info(f"[DEBUG] Starting get_quiz_questions_for_file for file_id: {file_id}")
        with self.get_unit_of_work() as uow:
            try:
                # First, verify the file exists and is processed
                logger.info(f"[DEBUG] Checking file {file_id} status")
                file = uow.session.query(File).filter(
                    File.id == file_id,
                    File.processing_status == 'processed'
                ).first()
                
                if not file:
                    logger.warning(f"[DEBUG] File {file_id} not found or not processed")
                    return []
                
                logger.info(f"[DEBUG] File {file_id} is processed. Querying quiz questions...")
                # Then get all quiz questions for this file
                quiz_questions_orm = uow.session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                ).all()
                
                logger.info(f"[DEBUG] Raw quiz questions query result: {quiz_questions_orm}")
                logger.info(f"[DEBUG] Found {len(quiz_questions_orm)} quiz questions for file {file_id}")
                
                # Convert to response models
                result = [QuizQuestionResponse.from_orm(q) for q in quiz_questions_orm]
                logger.info(f"[DEBUG] Converted to {len(result)} response models")
                return result
                
            except Exception as e:
                logger.error(f"[ERROR] ORM query for quiz questions failed: {e}", exc_info=True)
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