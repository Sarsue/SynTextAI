"""
Async Learning Material repository for managing learning content database operations.

This module mirrors the sync LearningMaterialRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Dict, Any
import logging

from .async_base_repository import AsyncBaseRepository

# Import ORM models from the new models module
from ..models import KeyConcept as KeyConceptORM, Flashcard as FlashcardORM, QuizQuestion as QuizQuestionORM

logger = logging.getLogger(__name__)


class AsyncLearningMaterialRepository(AsyncBaseRepository):
    """Async repository for learning material operations."""

    async def add_key_concept(self, file_id: int, key_concept_data: dict) -> Optional[dict]:
        """Add a new key concept for a file.

        Args:
            file_id: ID of the file this concept belongs to
            key_concept_data: Dictionary containing concept data

        Returns:
            Optional[dict]: Created key concept data, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_concept = KeyConceptORM(
                    file_id=file_id,
                    concept_title=key_concept_data.get('concept_title', ''),
                    concept_explanation=key_concept_data.get('concept_explanation', ''),
                    source_page_number=key_concept_data.get('source_page_number'),
                    source_video_timestamp_start_seconds=key_concept_data.get('source_video_timestamp_start_seconds'),
                    source_video_timestamp_end_seconds=key_concept_data.get('source_video_timestamp_end_seconds'),
                    is_custom=key_concept_data.get('is_custom', False)
                )
                session.add(new_concept)
                await session.flush()
                await session.refresh(new_concept)

                return {
                    'id': new_concept.id,
                    'file_id': new_concept.file_id,
                    'concept_title': new_concept.concept_title,
                    'concept_explanation': new_concept.concept_explanation,
                    'source_page_number': new_concept.source_page_number,
                    'source_video_timestamp_start_seconds': new_concept.source_video_timestamp_start_seconds,
                    'source_video_timestamp_end_seconds': new_concept.source_video_timestamp_end_seconds,
                    'is_custom': new_concept.is_custom,
                    'created_at': new_concept.created_at,
                    'updated_at': new_concept.updated_at
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding key concept: {e}", exc_info=True)
                return None

    async def get_key_concept_by_id(self, key_concept_id: int) -> Optional[KeyConceptORM]:
        """Get a key concept by its ID.

        Args:
            key_concept_id: ID of the key concept

        Returns:
            Optional[KeyConceptORM]: Key concept if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                return await session.get(KeyConceptORM, key_concept_id)
            except Exception as e:
                logger.error(f"Error getting key concept by ID {key_concept_id}: {e}", exc_info=True)
                return None

    async def count_key_concepts_for_file(self, file_id: int) -> int:
        """Count key concepts for a file.

        Args:
            file_id: ID of the file

        Returns:
            int: Number of key concepts for the file
        """
        async with self.get_async_session() as session:
            try:
                count = await session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).count()
                return count
            except Exception as e:
                logger.error(f"Error counting key concepts for file {file_id}: {e}", exc_info=True)
                return 0

    async def get_key_concepts_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[dict]:
        """Get key concepts for a file with pagination.

        Args:
            file_id: ID of the file
            page: Page number (1-based)
            page_size: Number of items per page

        Returns:
            List[dict]: List of key concept data
        """
        async with self.get_async_session() as session:
            try:
                offset = (page - 1) * page_size
                concepts_orm = await session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).offset(offset).limit(page_size).all()

                concepts = []
                for concept in concepts_orm:
                    concepts.append({
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
                    })

                return concepts
            except Exception as e:
                logger.error(f"Error getting key concepts for file {file_id}: {e}", exc_info=True)
                return []

    async def update_key_concept(self, concept_id: int, update_data: dict) -> Optional[dict]:
        """Update a key concept.

        Args:
            concept_id: ID of the key concept to update
            update_data: Dictionary containing fields to update

        Returns:
            Optional[dict]: Updated key concept data, or None if update failed
        """
        async with self.get_async_session() as session:
            try:
                concept = await session.get(KeyConceptORM, concept_id)
                if not concept:
                    return None

                # Update fields if provided
                if 'concept_title' in update_data:
                    concept.concept_title = update_data['concept_title']
                if 'concept_explanation' in update_data:
                    concept.concept_explanation = update_data['concept_explanation']

                await session.commit()
                await session.refresh(concept)

                return {
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
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating key concept {concept_id}: {e}", exc_info=True)
                return None

    async def delete_key_concept(self, key_concept_id: int, user_id: int) -> bool:
        """Delete a key concept by its ID.

        Args:
            key_concept_id: ID of the key concept to delete
            user_id: ID of the user making the request (for authorization)

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                concept = await session.get(KeyConceptORM, key_concept_id)
                if not concept:
                    return False

                # TODO: Add proper authorization check (ensure user owns the file)
                await session.delete(concept)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting key concept {key_concept_id}: {e}", exc_info=True)
                return False

    async def add_flashcard(self, file_id: int, flashcard_data: dict) -> Optional[dict]:
        """Add a new flashcard for a file.

        Args:
            file_id: ID of the file this flashcard belongs to
            flashcard_data: Dictionary containing flashcard data

        Returns:
            Optional[dict]: Created flashcard data, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_flashcard = FlashcardORM(
                    file_id=file_id,
                    question=flashcard_data.get('question', ''),
                    answer=flashcard_data.get('answer', ''),
                    key_concept_id=flashcard_data.get('key_concept_id'),
                    is_custom=flashcard_data.get('is_custom', False)
                )
                session.add(new_flashcard)
                await session.flush()
                await session.refresh(new_flashcard)

                return {
                    'id': new_flashcard.id,
                    'file_id': new_flashcard.file_id,
                    'question': new_flashcard.question,
                    'answer': new_flashcard.answer,
                    'key_concept_id': new_flashcard.key_concept_id,
                    'is_custom': new_flashcard.is_custom,
                    'created_at': new_flashcard.created_at,
                    'updated_at': new_flashcard.updated_at
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding flashcard: {e}", exc_info=True)
                return None

    async def count_flashcards_for_file(self, file_id: int) -> int:
        """Count flashcards for a file.

        Args:
            file_id: ID of the file

        Returns:
            int: Number of flashcards for the file
        """
        async with self.get_async_session() as session:
            try:
                count = await session.query(FlashcardORM).filter(
                    FlashcardORM.file_id == file_id
                ).count()
                return count
            except Exception as e:
                logger.error(f"Error counting flashcards for file {file_id}: {e}", exc_info=True)
                return 0

    async def get_flashcards_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[dict]:
        """Get flashcards for a file with pagination.

        Args:
            file_id: ID of the file
            page: Page number (1-based)
            page_size: Number of items per page

        Returns:
            List[dict]: List of flashcard data
        """
        async with self.get_async_session() as session:
            try:
                offset = (page - 1) * page_size
                flashcards_orm = await session.query(FlashcardORM).filter(
                    FlashcardORM.file_id == file_id
                ).offset(offset).limit(page_size).all()

                flashcards = []
                for flashcard in flashcards_orm:
                    flashcards.append({
                        'id': flashcard.id,
                        'file_id': flashcard.file_id,
                        'question': flashcard.question,
                        'answer': flashcard.answer,
                        'key_concept_id': flashcard.key_concept_id,
                        'is_custom': flashcard.is_custom,
                        'created_at': flashcard.created_at,
                        'updated_at': flashcard.updated_at
                    })

                return flashcards
            except Exception as e:
                logger.error(f"Error getting flashcards for file {file_id}: {e}", exc_info=True)
                return []

    async def get_flashcard_by_id(self, flashcard_id: int) -> Optional[Dict[str, Any]]:
        """Get a flashcard by its ID.

        Args:
            flashcard_id: ID of the flashcard

        Returns:
            Optional[Dict]: Flashcard data if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                flashcard = await session.get(FlashcardORM, flashcard_id)
                if not flashcard:
                    return None

                return {
                    'id': flashcard.id,
                    'file_id': flashcard.file_id,
                    'question': flashcard.question,
                    'answer': flashcard.answer,
                    'key_concept_id': flashcard.key_concept_id,
                    'is_custom': flashcard.is_custom,
                    'created_at': flashcard.created_at,
                    'updated_at': flashcard.updated_at
                }
            except Exception as e:
                logger.error(f"Error getting flashcard by ID {flashcard_id}: {e}", exc_info=True)
                return None

    async def update_flashcard(self, flashcard_id: int, user_id: int, update_data: dict) -> Optional[Dict[str, Any]]:
        """Update a flashcard.

        Args:
            flashcard_id: ID of the flashcard to update
            user_id: ID of the user making the request (for authorization)
            update_data: Dictionary containing fields to update

        Returns:
            Optional[Dict]: Updated flashcard data, or None if update failed
        """
        async with self.get_async_session() as session:
            try:
                flashcard = await session.get(FlashcardORM, flashcard_id)
                if not flashcard:
                    return None

                # TODO: Add proper authorization check

                # Update fields if provided
                if 'question' in update_data:
                    flashcard.question = update_data['question']
                if 'answer' in update_data:
                    flashcard.answer = update_data['answer']

                await session.commit()
                await session.refresh(flashcard)

                return {
                    'id': flashcard.id,
                    'file_id': flashcard.file_id,
                    'question': flashcard.question,
                    'answer': flashcard.answer,
                    'key_concept_id': flashcard.key_concept_id,
                    'is_custom': flashcard.is_custom,
                    'created_at': flashcard.created_at,
                    'updated_at': flashcard.updated_at
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating flashcard {flashcard_id}: {e}", exc_info=True)
                return None

    async def delete_flashcard(self, flashcard_id: int, user_id: int) -> bool:
        """Delete a flashcard by its ID.

        Args:
            flashcard_id: ID of the flashcard to delete
            user_id: ID of the user making the request (for authorization)

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                flashcard = await session.get(FlashcardORM, flashcard_id)
                if not flashcard:
                    return False

                # TODO: Add proper authorization check
                await session.delete(flashcard)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting flashcard {flashcard_id}: {e}", exc_info=True)
                return False

    async def add_quiz_question(self, file_id: int, **kwargs) -> Optional[dict]:
        """Add a new quiz question for a file.

        Args:
            file_id: ID of the file this question belongs to
            **kwargs: Question data (question, options, correct_answer, etc.)

        Returns:
            Optional[dict]: Created quiz question data, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_question = QuizQuestionORM(
                    file_id=file_id,
                    question=kwargs.get('question', ''),
                    question_type=kwargs.get('question_type', 'multiple_choice'),
                    options=kwargs.get('options', []),
                    correct_answer=kwargs.get('correct_answer', ''),
                    explanation=kwargs.get('explanation', ''),
                    key_concept_id=kwargs.get('key_concept_id'),
                    is_custom=kwargs.get('is_custom', False)
                )
                session.add(new_question)
                await session.flush()
                await session.refresh(new_question)

                return {
                    'id': new_question.id,
                    'file_id': new_question.file_id,
                    'question': new_question.question,
                    'question_type': new_question.question_type,
                    'options': new_question.options,
                    'correct_answer': new_question.correct_answer,
                    'explanation': new_question.explanation,
                    'key_concept_id': new_question.key_concept_id,
                    'is_custom': new_question.is_custom,
                    'created_at': new_question.created_at,
                    'updated_at': new_question.updated_at
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None

    async def count_quiz_questions_for_file(self, file_id: int) -> int:
        """Count quiz questions for a file.

        Args:
            file_id: ID of the file

        Returns:
            int: Number of quiz questions for the file
        """
        async with self.get_async_session() as session:
            try:
                count = await session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                ).count()
                return count
            except Exception as e:
                logger.error(f"Error counting quiz questions for file {file_id}: {e}", exc_info=True)
                return 0

    async def get_quiz_questions_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[dict]:
        """Get quiz questions for a file with pagination.

        Args:
            file_id: ID of the file
            page: Page number (1-based)
            page_size: Number of items per page

        Returns:
            List[dict]: List of quiz question data
        """
        async with self.get_async_session() as session:
            try:
                offset = (page - 1) * page_size
                questions_orm = await session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                ).offset(offset).limit(page_size).all()

                questions = []
                for question in questions_orm:
                    questions.append({
                        'id': question.id,
                        'file_id': question.file_id,
                        'question': question.question,
                        'question_type': question.question_type,
                        'options': question.options,
                        'distractors': question.distractors,
                        'correct_answer': question.correct_answer,
                        'explanation': question.explanation,
                        'key_concept_id': question.key_concept_id,
                        'is_custom': question.is_custom,
                        'created_at': question.created_at,
                        'updated_at': question.updated_at
                    })

                return questions
            except Exception as e:
                logger.error(f"Error getting quiz questions for file {file_id}: {e}", exc_info=True)
                return []

    async def get_quiz_question_by_id(self, quiz_question_id: int) -> Optional[dict]:
        """Get a quiz question by its ID.

        Args:
            quiz_question_id: ID of the quiz question

        Returns:
            Optional[dict]: Quiz question data if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                question = await session.get(QuizQuestionORM, quiz_question_id)
                if not question:
                    return None

                return {
                    'id': question.id,
                    'file_id': question.file_id,
                    'question': question.question,
                    'question_type': question.question_type,
                    'options': question.options,
                    'distractors': question.distractors,
                    'correct_answer': question.correct_answer,
                    'explanation': question.explanation,
                    'key_concept_id': question.key_concept_id,
                    'is_custom': question.is_custom,
                    'created_at': question.created_at,
                    'updated_at': question.updated_at
                }
            except Exception as e:
                logger.error(f"Error getting quiz question by ID {quiz_question_id}: {e}", exc_info=True)
                return None

    async def update_quiz_question(self, quiz_question_id: int, user_id: int, update_data: dict) -> Optional[Dict[str, Any]]:
        """Update a quiz question.

        Args:
            quiz_question_id: ID of the quiz question to update
            user_id: ID of the user making the request (for authorization)
            update_data: Dictionary containing fields to update

        Returns:
            Optional[Dict]: Updated quiz question data, or None if update failed
        """
        async with self.get_async_session() as session:
            try:
                question = await session.get(QuizQuestionORM, quiz_question_id)
                if not question:
                    return None

                # TODO: Add proper authorization check

                # Update fields if provided
                if 'question' in update_data:
                    question.question = update_data['question']
                if 'options' in update_data:
                    question.options = update_data['options']
                if 'correct_answer' in update_data:
                    question.correct_answer = update_data['correct_answer']
                if 'explanation' in update_data:
                    question.explanation = update_data['explanation']

                await session.commit()
                await session.refresh(question)

                return {
                    'id': question.id,
                    'file_id': question.file_id,
                    'question': question.question,
                    'question_type': question.question_type,
                    'options': question.options,
                    'distractors': question.distractors,
                    'correct_answer': question.correct_answer,
                    'explanation': question.explanation,
                    'key_concept_id': question.key_concept_id,
                    'is_custom': question.is_custom,
                    'created_at': question.created_at,
                    'updated_at': question.updated_at
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating quiz question {quiz_question_id}: {e}", exc_info=True)
                return None

    async def delete_quiz_question(self, quiz_question_id: int, user_id: int) -> bool:
        """Delete a quiz question by its ID.

        Args:
            quiz_question_id: ID of the quiz question to delete
            user_id: ID of the user making the request (for authorization)

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                question = await session.get(QuizQuestionORM, quiz_question_id)
                if not question:
                    return False

                # TODO: Add proper authorization check
                await session.delete(question)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting quiz question {quiz_question_id}: {e}", exc_info=True)
                return False
