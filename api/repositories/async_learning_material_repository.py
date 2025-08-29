"""
Async Learning Material Repository implementation.
Handles all database operations for LearningMaterial model using async SQLAlchemy.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
from sqlalchemy.future import select
from sqlalchemy import or_, delete, update
from sqlalchemy.orm import selectinload

from ..models.orm_models import (
    Flashcard as FlashcardModel,
    Flashcard,
    File,
    KeyConcept,
    QuizQuestion as QuizQuestionModel
)
from ..models.flashcard import FlashcardCreate, FlashcardUpdate
from ..models.quiz import QuizQuestionCreate, QuizQuestionUpdate
from .async_base_repository import AsyncBaseRepository
from .repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

class AsyncLearningMaterialRepository(AsyncBaseRepository[FlashcardModel, FlashcardCreate, FlashcardUpdate]):
    """Async repository for LearningMaterial model operations."""

    def __init__(self, repository_manager: RepositoryManager):
        super().__init__(FlashcardModel, repository_manager)
        self._repository_manager = repository_manager

    async def get_by_user(self, user_id: int) -> List[FlashcardModel]:
        """Get all learning materials for a specific user."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .join(File, FlashcardModel.file_id == File.id)
                .where(File.user_id == user_id)
                .order_by(FlashcardModel.id.desc())
            )
            return result.scalars().all()

    async def search(self, user_id: int, query: str, limit: int = 10) -> List[FlashcardModel]:
        """Search learning materials by content."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .join(File, FlashcardModel.file_id == File.id)
                .where(
                    (File.user_id == user_id) &
                    (
                        FlashcardModel.question.ilike(f"%{query}%") |
                        FlashcardModel.answer.ilike(f"%{query}%")
                    )
                )
                .order_by(FlashcardModel.id.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def get_by_key_concept(self, key_concept_id: int, user_id: int) -> List[FlashcardModel]:
        """Get all learning materials for a specific key concept and user."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .join(File, FlashcardModel.file_id == File.id)
                .where(
                    (FlashcardModel.key_concept_id == key_concept_id) &
                    (File.user_id == user_id)
                )
                .order_by(FlashcardModel.id.desc())
            )
            return result.scalars().all()

    async def create_custom_flashcard(
        self, user_id: int, file_id: int, question: str, answer: str,
        key_concept_id: Optional[int] = None
    ) -> FlashcardModel:
        """Create a new custom flashcard."""
        async with self.session_scope as session:
            file_result = await session.execute(
                select(File)
                .where((File.id == file_id) & (File.user_id == user_id))
            )
            if not file_result.scalars().first():
                raise ValueError("File not found or access denied")

            flashcard = FlashcardModel(
                file_id=file_id,
                key_concept_id=key_concept_id,
                question=question,
                answer=answer,
                is_custom=True,
                created_at=datetime.utcnow()
            )
            session.add(flashcard)
            await session.commit()
            await session.refresh(flashcard)
            return flashcard
            
    async def create_quiz_question(
        self, 
        file_id: int, 
        question: str, 
        question_type: str,
        correct_answer: str,
        key_concept_id: Optional[int] = None,
        distractors: Optional[List[str]] = None,
        quiz_question_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new quiz question.
        
        Args:
            file_id: ID of the file this question is associated with
            question: The question text
            question_type: Type of question (e.g., 'MCQ', 'TF')
            correct_answer: The correct answer to the question
            key_concept_id: Optional ID of the key concept this question relates to
            distractors: List of incorrect answer options (for multiple choice)
            quiz_question_data: Additional question data as a dictionary
            
        Returns:
            The created quiz question as a dictionary, or None if creation failed
        """
        async with self.session_scope as session:
            try:
                quiz_question = QuizQuestionModel(
                    file_id=file_id,
                    key_concept_id=key_concept_id,
                    question=question,
                    question_type=question_type,
                    correct_answer=correct_answer,
                    distractors=distractors or [],
                    quiz_question_data=quiz_question_data or {},
                    created_at=datetime.utcnow()
                )
                session.add(quiz_question)
                await session.commit()
                await session.refresh(quiz_question)
                return quiz_question.__dict__
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating quiz question: {e}", exc_info=True)
                return None

    async def get_learning_material_with_questions(self, material_id: int) -> Optional[FlashcardModel]:
        """Get a flashcard with its associated file and key concept."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .options(selectinload(FlashcardModel.file), selectinload(FlashcardModel.key_concept))
                .where(FlashcardModel.id == material_id)
            )
            return result.scalar_one_or_none()

    async def get_user_learning_materials(self, user_id: int, skip: int = 0, limit: int = 100) -> List[FlashcardModel]:
        """Get all flashcards for a specific user with pagination."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .join(File, File.id == FlashcardModel.file_id)
                .where(File.user_id == user_id)
                .offset(skip)
                .limit(limit)
                .options(selectinload(FlashcardModel.file), selectinload(FlashcardModel.key_concept))
            )
            return result.scalars().all()

    async def create_learning_material(self, learning_material_data: Dict[str, Any], user_id: int) -> FlashcardModel:
        """Create a new flashcard."""
        async with self.session_scope as session:
            flashcard = FlashcardModel(**learning_material_data, created_at=datetime.utcnow())
            session.add(flashcard)
            await session.commit()
            await session.refresh(flashcard)
            return flashcard

    async def update_learning_material(self, material_id: int, update_data: Dict[str, Any]) -> Optional[FlashcardModel]:
        """Update a flashcard."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .where(FlashcardModel.id == material_id)
                .options(selectinload(FlashcardModel.file), selectinload(FlashcardModel.key_concept))
            )
            flashcard = result.scalar_one_or_none()
            if flashcard:
                for key, value in update_data.items():
                    setattr(flashcard, key, value)
                await session.commit()
                await session.refresh(flashcard)
            return flashcard

    async def delete_learning_material(self, material_id: int) -> bool:
        """Delete a flashcard by ID."""
        async with self.session_scope as session:
            result = await session.execute(
                delete(FlashcardModel)
                .where(FlashcardModel.id == material_id)
                .returning(FlashcardModel.id)
            )
            await session.commit()
            return result.scalar_one_or_none() is not None

    async def get_learning_material_by_id(self, material_id: int) -> Optional[FlashcardModel]:
        """Get a flashcard by ID with related data."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FlashcardModel)
                .options(selectinload(FlashcardModel.file), selectinload(FlashcardModel.key_concept))
                .where(FlashcardModel.id == material_id)
            )
            return result.scalar_one_or_none()

    # --- Key Concept Methods ---
    
    async def add_key_concept(self, file_id: int, key_concept_data: dict) -> Optional[Dict[str, Any]]:
        """Add a new key concept."""
        async with self.session_scope as session:
            try:
                key_concept = KeyConcept(
                    file_id=file_id,
                    concept_title=key_concept_data.get("concept_title"),
                    concept_explanation=key_concept_data.get("concept_explanation"),
                    source_page_number=key_concept_data.get("source_page_number"),
                    source_video_timestamp_start_seconds=key_concept_data.get("source_video_timestamp_start_seconds"),
                    source_video_timestamp_end_seconds=key_concept_data.get("source_video_timestamp_end_seconds"),
                    is_custom=key_concept_data.get("is_custom", False)
                )
                session.add(key_concept)
                await session.commit()
                await session.refresh(key_concept)
                return key_concept.__dict__
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding key concept: {e}", exc_info=True)
                return None

    async def get_key_concept_by_id(self, key_concept_id: int) -> Optional[Dict[str, Any]]:
        """Get a key concept by its ID."""
        async with self.session_scope as session:
            try:
                result = await session.execute(
                    select(KeyConcept)
                    .where(KeyConcept.id == key_concept_id)
                    .options(selectinload(KeyConcept.file))
                )
                key_concept = result.scalar_one_or_none()
                if not key_concept:
                    return None
                return key_concept.__dict__
            except Exception as e:
                logger.error(f"Error getting key concept {key_concept_id}: {e}", exc_info=True)
                return None

    async def get_key_concepts_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[Dict[str, Any]]:
        """Get paginated key concepts for a file.
        
        Args:
            file_id: ID of the file to get concepts for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of key concept dictionaries
        """
        async with self.session_scope as session:
            try:
                offset = (page - 1) * page_size
                result = await session.execute(
                    select(KeyConcept)
                    .where(KeyConcept.file_id == file_id)
                    .order_by(KeyConcept.created_at.desc())
                    .offset(offset)
                    .limit(page_size)
                )
                return [kc.__dict__ for kc in result.scalars().all()]
            except Exception as e:
                logger.error(f"Error getting key concepts for file {file_id}: {e}", exc_info=True)
                return []
                
    async def count_key_concepts_for_file(self, file_id: int) -> int:
        """Count total number of key concepts for a file.
        
        Args:
            file_id: ID of the file to count concepts for
            
        Returns:
            Total count of key concepts for the file
        """
        from sqlalchemy import func
        
        async with self.session_scope as session:
            try:
                result = await session.execute(
                    select(func.count())
                    .select_from(KeyConcept)
                    .where(KeyConcept.file_id == file_id)
                )
                return result.scalar_one() or 0
            except Exception as e:
                logger.error(f"Error counting key concepts for file {file_id}: {e}", exc_info=True)
                return 0

    async def update_key_concept(self, key_concept_id: int, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a key concept."""
        async with self.session_scope as session:
            try:
                result = await session.execute(
                    select(KeyConcept)
                    .where(KeyConcept.id == key_concept_id)
                )
                key_concept = result.scalar_one_or_none()
                if not key_concept:
                    return None
                for field, value in update_data.items():
                    if hasattr(key_concept, field) and value is not None:
                        setattr(key_concept, field, value)
                key_concept.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(key_concept)
                return key_concept.__dict__
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating key concept {key_concept_id}: {e}", exc_info=True)
                return None

    async def delete_key_concept(self, key_concept_id: int, user_id: int) -> bool:
        """Delete a key concept."""
        async with self.session_scope as session:
            try:
                result = await session.execute(
                    select(KeyConcept)
                    .join(File, KeyConcept.file_id == File.id)
                    .where((KeyConcept.id == key_concept_id) & (File.user_id == user_id))
                )
                key_concept = result.scalar_one_or_none()
                if not key_concept:
                    return False
                await session.delete(key_concept)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting key concept {key_concept_id}: {e}", exc_info=True)
                return False

    # --- Quiz Methods ---
    
    async def add_quiz_question(
        self, file_id: int, question: str, question_type: str, correct_answer: str,
        key_concept_id: Optional[int] = None, distractors: Optional[List[Dict[str, Any]]] = None,
        is_custom: bool = False
    ) -> QuizQuestionModel:
        """Add a new quiz question."""
        async with self.session_scope as session:
            quiz_question = QuizQuestionModel(
                file_id=file_id,
                key_concept_id=key_concept_id,
                question=question,
                question_type=question_type,
                correct_answer=correct_answer,
                distractors=distractors or [],
                is_custom=is_custom,
                created_at=datetime.utcnow()
            )
            session.add(quiz_question)
            await session.commit()
            await session.refresh(quiz_question)
            return quiz_question

    async def get_quiz_question_by_id(self, question_id: int) -> Optional[QuizQuestionModel]:
        """Get a quiz question by its ID."""
        async with self.session_scope as session:
            result = await session.execute(
                select(QuizQuestionModel).where(QuizQuestionModel.id == question_id)
            )
            return result.scalar_one_or_none()

    async def count_quiz_questions_for_file(self, file_id: int) -> int:
        """Count total number of quiz questions for a file.
        
        Args:
            file_id: ID of the file to count questions for
            
        Returns:
            Total count of quiz questions for the file
        """
        from sqlalchemy import func
        async with self.session_scope as session:
            result = await session.execute(
                select(func.count())
                .select_from(QuizQuestionModel)
                .where(QuizQuestionModel.file_id == file_id)
            )
            return result.scalar_one() or 0
            
    async def get_quiz_questions_for_file(
        self, 
        file_id: int, 
        page: int = 1, 
        page_size: int = 10
    ) -> List[Dict[str, Any]]:
        """Get paginated quiz questions for a specific file.
        
        Args:
            file_id: ID of the file to get questions for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of quiz question dictionaries
        """
        async with self.session_scope as session:
            offset = (page - 1) * page_size
            result = await session.execute(
                select(QuizQuestionModel)
                .where(QuizQuestionModel.file_id == file_id)
                .order_by(QuizQuestionModel.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            return [q.__dict__ for q in result.scalars().all()]

    async def update_quiz_question(self, question_id: int, update_data: Dict[str, Any]) -> Optional[QuizQuestionModel]:
        """Update a quiz question."""
        async with self.session_scope as session:
            question = await self.get_quiz_question_by_id(question_id)
            if not question:
                return None
            for field, value in update_data.items():
                if hasattr(question, field) and value is not None:
                    setattr(question, field, value)
            question.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(question)
            return question

    async def count_flashcards_for_file(self, file_id: int) -> int:
        """Count total number of flashcards for a file.
        
        Args:
            file_id: ID of the file to count flashcards for
            
        Returns:
            Total count of flashcards for the file
        """
        from sqlalchemy import func
        async with self.session_scope as session:
            result = await session.execute(
                select(func.count())
                .select_from(FlashcardModel)
                .where(FlashcardModel.file_id == file_id)
            )
            return result.scalar_one() or 0
            
    async def get_flashcards_for_file(
        self, 
        file_id: int, 
        page: int = 1, 
        page_size: int = 10
    ) -> List[Dict[str, Any]]:
        """Get paginated flashcards for a specific file.
        
        Args:
            file_id: ID of the file to get flashcards for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of flashcard dictionaries
        """
        async with self.session_scope as session:
            offset = (page - 1) * page_size
            result = await session.execute(
                select(FlashcardModel)
                .where(FlashcardModel.file_id == file_id)
                .order_by(FlashcardModel.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            return [f.__dict__ for f in result.scalars().all()]

    async def delete_quiz_question(self, question_id: int) -> bool:
        """Delete a quiz question by ID."""
        async with self.session_scope as session:
            result = await session.execute(
                delete(QuizQuestionModel)
                .where(QuizQuestionModel.id == question_id)
                .returning(QuizQuestionModel.id)
            )
            await session.commit()
            return result.scalar_one_or_none() is not None
