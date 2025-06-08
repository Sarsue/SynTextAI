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
        
    def add_key_concept(
        self,
        file_id: int,
        concept_title: str,
        concept_explanation: str,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Add a new key concept associated with a file.
        
        Args:
            file_id: ID of the file this concept belongs to
            concept_title: Title/name of the concept
            concept_explanation: Detailed explanation of the concept
            source_page_number: Page number for PDF sources
            source_video_timestamp_start_seconds: Start timestamp for video sources
            source_video_timestamp_end_seconds: End timestamp for video sources

        
        Returns:
            int: The ID of the newly created concept, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                logger.debug(f"Creating key concept: title='{concept_title[:50]}...', file_id={file_id}")
                new_concept = KeyConceptORM(
                    file_id=file_id,
                    concept_title=concept_title,
                    concept_explanation=concept_explanation,
                    source_page_number=source_page_number,
                    source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
                    source_video_timestamp_end_seconds=source_video_timestamp_end_seconds,
                )
                uow.session.add(new_concept)
                uow.session.flush()  # Make sure we have an ID before continuing
                concept_id = new_concept.id
                
                # Explicitly commit to ensure transaction completion
                uow.session.commit()
                logger.info(f"Successfully added key concept '{concept_title[:50]}...' for file {file_id}, id={concept_id}")
                return concept_id
            except Exception as e:
                # Explicitly rollback on error
                uow.session.rollback()
                logger.error(f"Error adding key concept '{concept_title[:50]}...': {e}", exc_info=True)
                return None
    
    # Flashcard functionality removed as it no longer exists in the DB schema
    
    # Quiz question functionality removed as it no longer exists in the DB schema
    
    def get_key_concepts_for_file(self, file_id: int) -> List[KeyConcept]:
        """Get key concepts for a file using ORM.
        
        Args:
            file_id: ID of the file
            
        Returns:
            A list of KeyConcept domain model objects
        """
        with self.get_unit_of_work() as uow:
            key_concepts = []
            
            try:
                # Use ORM query with correct field names
                logger.debug(f"Fetching key concepts for file_id {file_id} using ORM")
                concepts_orm = uow.session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).all()
                
                # Convert ORM models to domain models
                for concept in concepts_orm:
                    kc = KeyConcept(
                        id=concept.id,
                        file_id=concept.file_id,
                        concept_title=getattr(concept, 'concept_title', ''),  # Using correct field name
                        concept_explanation=getattr(concept, 'concept_explanation', ''),  # Using correct field name

                        source_page_number=getattr(concept, 'source_page_number', None),
                        source_video_timestamp_start_seconds=getattr(concept, 'source_video_timestamp_start_seconds', None),
                        source_video_timestamp_end_seconds=getattr(concept, 'source_video_timestamp_end_seconds', None),
                        created_at=concept.created_at if hasattr(concept, 'created_at') else datetime.now()
                    )
                    key_concepts.append(kc)
                
                logger.debug(f"Found {len(key_concepts)} key concepts for file_id {file_id} using ORM")
                return key_concepts
                
            except Exception as e:
                logger.error(f"ORM query for key concepts failed: {e}")
                raise
    
    # Flashcard methods
    def add_flashcard(self, file_id: int, question: str, answer: str, key_concept_id: Optional[int] = None, is_custom: bool = False) -> Optional[int]:
        """Add a new flashcard.
        
        Args:
            file_id: ID of the file associated with this flashcard
            question: Question text
            answer: Answer text
            key_concept_id: Optional ID of the associated key concept
            is_custom: Whether this is a custom flashcard (vs AI-generated)
            
        Returns:
            int: The ID of the newly created flashcard, or None if creation failed
        """
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
                # Commit handled by UnitOfWork
                logger.info(f"Added flashcard for file {file_id}")
                return new_flashcard.id
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error adding flashcard: {e}", exc_info=True)
                return None
    
    def get_flashcards_for_file(self, file_id: int) -> List[Flashcard]:
        """Get flashcards for a file by ID.
        
        Args:
            file_id: The ID of the file
            
        Returns:
            A list of Flashcard objects
        """
        if not isinstance(file_id, int):
            raise ValueError(f"file_id must be an integer, got {type(file_id)}")

        with self.get_unit_of_work() as uow:
            flashcards = []
            
            try:
                # Use ORM query
                logger.debug(f"Fetching flashcards for file_id {file_id} using ORM")
                flashcards_orm = uow.session.query(FlashcardORM).filter(
                    FlashcardORM.file_id == file_id
                ).all()
                
                # Convert to domain models
                for fc_orm in flashcards_orm:
                    fc = Flashcard(
                        id=fc_orm.id,
                        file_id=fc_orm.file_id,
                        key_concept_id=getattr(fc_orm, 'key_concept_id', None),
                        question=fc_orm.question,
                        answer=fc_orm.answer,
                        is_custom=getattr(fc_orm, 'is_custom', False),
                        created_at=fc_orm.created_at if hasattr(fc_orm, 'created_at') else datetime.now()
                    )
                    flashcards.append(fc)
                
                logger.debug(f"Found {len(flashcards)} flashcards for file_id {file_id} using ORM")
            except Exception as e:
                logger.error(f"ORM query for flashcards failed: {e}")
                raise

            return flashcards
            
    # Quiz questions methods
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
        """Add a new quiz question.
        
        Args:
            file_id: ID of the file associated with this quiz question
            question: Question text
            question_type: Type of question (MCQ, TF, etc.)
            correct_answer: The correct answer
            distractors: List of incorrect answer choices
            key_concept_id: Optional ID of associated key concept
            is_custom: Whether this is a custom question (vs AI-generated)
            
        Returns:
            int: The ID of the newly created quiz question, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                new_quiz = QuizQuestionORM(
                    file_id=file_id,
                    question=question,  # Updated field name
                    question_type=question_type,  # Added required field
                    correct_answer=correct_answer,
                    distractors=distractors,
                    key_concept_id=key_concept_id,  # Added field
                    # Note: explanation and difficulty fields removed as they don't exist in DB
                )
                uow.session.add(new_quiz)
                # Commit handled by UnitOfWork
                logger.info(f"Added quiz question for file {file_id}")
                return new_quiz.id
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None
    
    def get_quiz_questions_for_file(self, file_id: int) -> List[QuizQuestion]:
        """Get quiz questions for a file by ID.

        Args:
            file_id: The ID of the file

        Returns:
            A list of QuizQuestion objects
        """
        if not isinstance(file_id, int):
            raise ValueError(f"file_id must be an integer, got {type(file_id)}")

        with self.get_unit_of_work() as uow:
            quizzes = []

            # Using SQLAlchemy ORM for safer and more maintainable queries
            try:
                logger.debug(f"Fetching quiz questions for file_id {file_id} using ORM")
                quizzes_orm = uow.session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                ).all()
                
                for q_orm in quizzes_orm:
                    # Set default question_type if not in DB
                    question_type = getattr(q_orm, 'question_type', 'MCQ')
                    
                    # Make sure all fields expected by frontend are present
                    q = QuizQuestion(
                        id=q_orm.id,
                        file_id=q_orm.file_id,
                        key_concept_id=getattr(q_orm, 'key_concept_id', None),
                        question=q_orm.question,
                        question_type=question_type,
                        correct_answer=q_orm.correct_answer,
                        distractors=q_orm.distractors if q_orm.distractors else [],
                        created_at=q_orm.created_at if hasattr(q_orm, 'created_at') else datetime.now(),
                        # Fields needed by frontend even if not in DB
                        explanation=None,
                        difficulty="medium", # Default value expected by frontend
                        is_custom=False
                    )
                    quizzes.append(q)
                
                logger.debug(f"Found {len(quizzes)} quiz questions for file_id {file_id} using ORM")
            except Exception as e:
                logger.error(f"ORM query for quiz questions failed: {e}")
                raise

            return quizzes
