"""
Learning Material Repository
===========================

This module provides a repository pattern implementation for managing learning materials
including key concepts, flashcards, and quiz questions. It serves as an abstraction layer
between the database and the application's business logic.

Key Features:
- CRUD operations for key concepts, flashcards, and quiz questions
- Support for file-based organization of learning materials
- Pagination for efficient data retrieval
- Transaction management for data consistency
- Comprehensive error handling and logging

Dependencies:
- SQLAlchemy for ORM operations
- Pydantic for data validation and serialization
- Logging for operation tracking

Example Usage:
    ```python
    from api.repositories.learning_material_repository import LearningMaterialRepository
    from api.schemas.learning_content import KeyConceptCreate
    
    # Initialize the repository
    repo = LearningMaterialRepository()
    
    # Add a new key concept
    concept_data = KeyConceptCreate(
        concept_title="Machine Learning",
        concept_explanation="A field of AI that enables systems to learn from data.",
        source_page_number=42
    )
    new_concept = repo.add_key_concept(file_id=1, key_concept_data=concept_data)
    
    # Retrieve key concepts for a file
    concepts = repo.get_key_concepts_for_file(file_id=1, page=1, page_size=10)
    ```
"""
from typing import List, Optional, Dict, Any
from datetime import datetime

from api.models.orm_models import File  # Add this import
import logging

from sqlalchemy.orm import selectinload

from ..models import File, Flashcard as FlashcardORM, KeyConcept as KeyConceptORM, QuizQuestion as QuizQuestionORM
from ..schemas.learning_content import (
    KeyConceptCreate, KeyConceptUpdate, KeyConceptResponse,
    FlashcardCreate, FlashcardUpdate, FlashcardResponse,
    QuizQuestionCreate, QuizQuestionUpdate, QuizQuestionResponse
)
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class LearningMaterialRepository(BaseRepository):
    """
    A repository class that provides an interface for managing learning materials.
    
    This class handles all database operations related to learning materials including
    key concepts, flashcards, and quiz questions. It provides a clean separation
    between the database layer and the business logic.
    
    Key Features:
    - CRUD operations for key concepts, flashcards, and quiz questions
    - Support for pagination and filtering
    - Transaction management
    - Comprehensive error handling and logging
    
    The repository uses SQLAlchemy's Unit of Work pattern for managing database
    sessions and transactions. All database operations are wrapped in transactions
    that are automatically committed on success or rolled back on failure.
    
    Example:
        ```python
        # Initialize the repository
        repo = LearningMaterialRepository()
        
        # Add a new flashcard
        flashcard_data = FlashcardCreate(
            front="What is machine learning?",
            back="A field of AI that enables systems to learn from data.",
            source_page_number=42
        )
        new_flashcard = repo.add_flashcard(file_id=1, flashcard_data=flashcard_data)
        
        # Get flashcards with pagination
        flashcards = repo.get_flashcards_for_file(file_id=1, page=1, page_size=10)
        ```
    """
    
    def __repr__(self) -> str:
        """
        Return a string representation of the repository.
        
        Returns:
            str: A string containing the repository class name and database URL
        """
        return f"<LearningMaterialRepository({self.database_url})>"
        
    # --- Key Concept Methods ---
    
    def add_key_concept(self, file_id: int, key_concept_data: KeyConceptCreate) -> Optional[dict]:
        """
        Add a new key concept to the database and return its details.
        
        This method creates a new key concept record in the database, associating it with
        the specified file. It handles both the new field names (concept_title, concept_explanation)
        and maintains backward compatibility with the old field names (concept, explanation).
        
        Args:
            file_id: The ID of the file to associate with the key concept.
                     Must be a valid file ID that exists in the database.
            key_concept_data: A Pydantic model (KeyConceptCreate) containing the key concept data.
                            Should include at minimum either (concept_title or concept) and
                            (concept_explanation or explanation).
            
        Returns:
            Optional[dict]: A dictionary containing the created key concept data if successful,
                         or None if the operation fails.
                         
                         The returned dictionary includes:
                         - id: The unique identifier of the key concept
                         - file_id: The associated file ID
                         - concept_title/concept: The title of the concept
                         - concept_explanation/explanation: Detailed explanation of the concept
                         - source_page_number: Page number where the concept appears (if applicable)
                         - source_video_timestamp_start_seconds: Start timestamp for video content (if applicable)
                         - source_video_timestamp_end_seconds: End timestamp for video content (if applicable)
                         - is_custom: Boolean indicating if the concept was manually created
                         - created_at: Timestamp of creation
                         - updated_at: Timestamp of last update
        
        Raises:
            ValueError: If required fields (concept_title/concept or 
                       concept_explanation/explanation) are missing or empty.
            SQLAlchemyError: If there's an error during database operations.
            
        Example:
            ```python
            from api.schemas.learning_content import KeyConceptCreate
            
            # Create a new key concept
            concept_data = KeyConceptCreate(
                concept_title="Neural Networks",
                concept_explanation="Computing systems inspired by biological neural networks...",
                source_page_number=42,
                is_custom=False
            )
            result = repo.add_key_concept(file_id=123, key_concept_data=concept_data)
            ```
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

    def count_key_concepts_for_file(self, file_id: int) -> int:
        """Count the total number of key concepts for a file.
        
        Args:
            file_id: The ID of the file to count key concepts for
            
        Returns:
            int: The total number of key concepts for the file
        """
        with self.get_unit_of_work() as uow:
            try:
                # Count key concepts for the file
                count = uow.session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).count()
                
                logger.info(f"[DEBUG] Counted {count} key concepts for file {file_id}")
                return count
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to count key concepts for file {file_id}: {e}", exc_info=True)
                return 0
    
    def get_key_concepts_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[KeyConceptResponse]:
        """Get key concepts for a file only if it has been processed.
        
        Args:
            file_id: The ID of the file to get key concepts for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of KeyConceptResponse objects
        """
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
                # Get key concepts with pagination
                query = uow.session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                )
                
                # Apply pagination
                offset = (page - 1) * page_size
                key_concepts_orm = query.offset(offset).limit(page_size).all()
                
                logger.info(f"[DEBUG] Raw key concepts query result: {key_concepts_orm}")
                logger.info(f"[DEBUG] Found {len(key_concepts_orm)} key concepts for file {file_id} (page {page}, size {page_size})")
                
                # Convert to response models
                result = [KeyConceptResponse.from_orm(kc) for kc in key_concepts_orm]
                logger.info(f"[DEBUG] Converted to {len(result)} response models")
                return result
                
            except Exception as e:
                logger.error(f"Error getting key concepts for file {file_id}: {e}", exc_info=True)
                return []

    def get_key_concept_by_id(self, key_concept_id: int) -> Optional[KeyConceptORM]:
        """Get a single key concept by its ID."""
        with self.get_unit_of_work() as uow:
            return uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == key_concept_id).first()

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
                concept = uow.session.query(KeyConceptORM).filter(KeyConceptORM.id == concept_id).one_or_none()
                if not concept:
                    logger.warning(f"Update failed: KeyConcept {concept_id} not found.")
                    return None
                
                # Store current values before updating
                current_values = {
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
                
                # Update fields from the update_data model
                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    setattr(concept, key, value)
                
                concept.updated_at = datetime.utcnow()
                uow.session.commit()
                
                # Create result from current values and updates to ensure we don't access detached objects
                result = {
                    **current_values,
                    **update_dict,
                    'concept': update_dict.get('concept_title', current_values['concept_title']),  # For backward compatibility
                    'explanation': update_dict.get('concept_explanation', current_values['concept_explanation']),  # For backward compatibility
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
                concept_orm = uow.session.query(KeyConceptORM).join(File).filter(
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

    def add_flashcard(self, file_id: int, flashcard_data: FlashcardCreate) -> Optional[Dict[str, Any]]:
        """
        Add a new flashcard from a Pydantic model and return the flashcard data as a dictionary.
        
        Args:
            file_id: The ID of the file to associate with the flashcard
            flashcard_data: Pydantic model containing flashcard data
            
        Returns:
            dict: A dictionary containing the flashcard data, or None if creation failed
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
                
                logger.info(f"Successfully added flashcard for file {file_id}, id={new_flashcard.id}")
                
                # Return the flashcard data as a dictionary to avoid session detachment issues
                # Only include fields that exist in the Flashcard model
                flashcard_dict = {
                    'id': new_flashcard.id,
                    'file_id': new_flashcard.file_id,
                    'question': new_flashcard.question,
                    'answer': new_flashcard.answer,
                    'key_concept_id': new_flashcard.key_concept_id,
                    'is_custom': new_flashcard.is_custom,
                    'created_at': new_flashcard.created_at
                }
                
                # Add updated_at only if it exists
                if hasattr(new_flashcard, 'updated_at'):
                    flashcard_dict['updated_at'] = new_flashcard.updated_at
                    
                return flashcard_dict
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding flashcard: {e}", exc_info=True)
                return None
    
    def count_flashcards_for_file(self, file_id: int) -> int:
        """Count the total number of flashcards for a file.
        
        Args:
            file_id: The ID of the file to count flashcards for
            
        Returns:
            int: The total number of flashcards for the file
        """
        with self.get_unit_of_work() as uow:
            try:
                # Count flashcards for the file
                count = uow.session.query(FlashcardORM).filter(
                    FlashcardORM.file_id == file_id
                ).count()
                
                logger.info(f"[DEBUG] Counted {count} flashcards for file {file_id}")
                return count
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to count flashcards for file {file_id}: {e}", exc_info=True)
                return 0
    
    def get_flashcards_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[FlashcardResponse]:
        """Get flashcards for a file by ID, only if it has been processed.
        
        Args:
            file_id: The ID of the file to get flashcards for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of FlashcardResponse objects
        """
        logger.info(f"[DEBUG] Starting get_flashcards_for_file for file_id: {file_id}")
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
                
                logger.info(f"[DEBUG] File {file_id} is processed. Querying flashcards...")
                # Get flashcards with pagination
                query = uow.session.query(FlashcardORM).filter(
                    FlashcardORM.file_id == file_id
                )
                
                # Apply pagination
                offset = (page - 1) * page_size
                flashcards_orm = query.offset(offset).limit(page_size).all()
                
                logger.info(f"[DEBUG] Found {len(flashcards_orm)} flashcards for file {file_id} (page {page}, size {page_size})")
                
                # Convert to response models
                result = [FlashcardResponse.from_orm(f) for f in flashcards_orm]
                logger.info(f"[DEBUG] Converted to {len(result)} flashcard response models")
                return result
                
            except Exception as e:
                logger.error(f"[ERROR] ORM query for flashcards failed: {e}", exc_info=True)
                return []
            
    def get_flashcard_by_id(self, flashcard_id: int) -> Optional[Dict[str, Any]]:
        """Get a single flashcard by its ID.
        
        Returns:
            Dictionary with flashcard data or None if not found
        """
        with self.get_unit_of_work() as uow:
            try:
                flashcard = (uow.session.query(FlashcardORM)
                    .options(selectinload('*'))
                    .filter(FlashcardORM.id == flashcard_id)
                    .first())
                
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
                    'updated_at': flashcard.updated_at,
                    'difficulty': getattr(flashcard, 'difficulty', 'medium')
                }
            except Exception as e:
                logger.error(f"Error getting flashcard {flashcard_id}: {e}", exc_info=True)
                return None

    def update_flashcard(self, flashcard_id: int, user_id: int, update_data: FlashcardUpdate) -> Optional[Dict[str, Any]]:
        """Update a flashcard's details from a Pydantic model, ensuring user ownership.
        
        Args:
            flashcard_id: The ID of the flashcard to update
            user_id: The ID of the user making the request
            update_data: Pydantic model containing the updates
            
        Returns:
            Dictionary with the updated flashcard data or None if not found
        """
        logger.info(f"[DEBUG] Updating flashcard {flashcard_id} by user {user_id}")
        with self.get_unit_of_work() as uow:
            try:
                # Verify the flashcard exists and belongs to the user
                flashcard = (uow.session.query(FlashcardORM)
                    .join(File, FlashcardORM.file_id == File.id)
                    .filter(
                        FlashcardORM.id == flashcard_id,
                        File.user_id == user_id
                    ).first())

                if not flashcard:
                    logger.warning(f"Update failed: Flashcard {flashcard_id} not found or user {user_id} lacks ownership.")
                    return None
                
                # Update fields from the update_data
                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    if hasattr(flashcard, key):
                        setattr(flashcard, key, value)
                
                # Store current values before updating
                current_values = {
                    'id': flashcard.id,
                    'file_id': flashcard.file_id,
                    'question': flashcard.question,
                    'answer': flashcard.answer,
                    'key_concept_id': flashcard.key_concept_id,
                    'is_custom': flashcard.is_custom,
                    'created_at': flashcard.created_at,
                    'updated_at': datetime.utcnow(),  # Use the new timestamp
                    'difficulty': getattr(flashcard, 'difficulty', 'medium')
                }
                
                # Update the updated_at timestamp
                flashcard.updated_at = current_values['updated_at']
                
                uow.session.commit()
                
                logger.info(f"Successfully updated flashcard {flashcard_id} by user {user_id}")
                
                # Return the updated flashcard data using the stored values
                return {
                    **current_values,
                    **update_data.dict(exclude_unset=True)  # Include any updated fields
                }
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating flashcard {flashcard_id}: {e}", exc_info=True)
                raise

    def delete_flashcard(self, flashcard_id: int, user_id: int) -> bool:
        """Delete a flashcard by its ID, ensuring user ownership.
        
        Args:
            flashcard_id: The ID of the flashcard to delete
            user_id: The ID of the user making the request
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        logger.info(f"[DEBUG] Deleting flashcard {flashcard_id} by user {user_id}")
        with self.get_unit_of_work() as uow:
            try:
                # Verify the flashcard exists and belongs to the user
                flashcard = (uow.session.query(FlashcardORM)
                    .join(File, FlashcardORM.file_id == File.id)
                    .filter(
                        FlashcardORM.id == flashcard_id,
                        File.user_id == user_id
                    ).first())

                if not flashcard:
                    logger.warning(f"Delete failed: Flashcard {flashcard_id} not found or user {user_id} lacks ownership.")
                    return False
                
                logger.debug(f"[DEBUG] Deleting flashcard: {flashcard.id} - {flashcard.question}")

                # Delete the flashcard
                uow.session.delete(flashcard)
                uow.session.commit()
                
                logger.info(f"Successfully deleted flashcard {flashcard_id} by user {user_id}")
                return True
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting flashcard {flashcard_id}: {e}", exc_info=True)
                raise
           
    # --- Quiz Question Methods ---

    def add_quiz_question(self, file_id: int, *args, **kwargs) -> Optional[QuizQuestionORM]:
        """
        Add a new quiz question and return the ORM instance.
        
        This method supports two calling conventions:
        1. New style (preferred):
           add_quiz_question(file_id, quiz_question_data=QuizQuestionCreate(...))
           
        2. Old style (for backward compatibility):
           add_quiz_question(file_id, key_concept_id, question, question_type, correct_answer, distractors)
        
        Args:
            file_id: The ID of the file to associate with the quiz question
            *args: For backward compatibility with old-style calls
            **kwargs: Either contains 'quiz_question_data' (new style) or individual fields (old style)
            
        Returns:
            QuizQuestionORM: The newly created quiz question ORM instance, or None if creation failed
        """
        # Handle old-style call (individual parameters)
        if len(args) >= 5 or 'question' in kwargs:
            # Old style call - extract parameters from args/kwargs
            if len(args) >= 5:
                # Positional args: file_id, key_concept_id, question, question_type, correct_answer, [distractors]
                key_concept_id = args[0] if len(args) > 0 else None
                question = args[1] if len(args) > 1 else kwargs.get('question', '')
                question_type = args[2] if len(args) > 2 else kwargs.get('question_type', 'MCQ')
                correct_answer = args[3] if len(args) > 3 else kwargs.get('correct_answer', '')
                distractors = args[4] if len(args) > 4 else kwargs.get('distractors', [])
            else:
                # Keyword args
                question = kwargs.get('question', '')
                question_type = kwargs.get('question_type', 'MCQ')
                correct_answer = kwargs.get('correct_answer', '')
                distractors = kwargs.get('distractors', [])
                key_concept_id = kwargs.get('key_concept_id')
                
            # Create a QuizQuestionCreate object from the individual parameters
            quiz_question_data = QuizQuestionCreate(
                question=question,
                question_type=question_type,
                correct_answer=correct_answer,
                distractors=distractors or [],
                key_concept_id=key_concept_id,
                is_custom=kwargs.get('is_custom', True)
            )
        else:
            # New style call - get the quiz_question_data object
            quiz_question_data = kwargs.get('quiz_question_data')
            if not quiz_question_data:
                raise ValueError("Missing required parameter 'quiz_question_data'")
                
            # If it's a dict, convert to Pydantic model
            if isinstance(quiz_question_data, dict):
                quiz_question_data = QuizQuestionCreate(**quiz_question_data)
        with self.get_unit_of_work() as uow:
            try:
                # Log the incoming data for debugging
                logger.debug(f"Creating new quiz question for file {file_id} with data: {quiz_question_data.dict()}")
                
                # Validate required fields
                if not quiz_question_data.question:
                    raise ValueError("Question is required")
                if not quiz_question_data.correct_answer:
                    raise ValueError("Correct answer is required")
                
                # Ensure distractors is a list and handle None/empty cases
                if quiz_question_data.distractors is None:
                    quiz_question_data.distractors = []
                elif not isinstance(quiz_question_data.distractors, list):
                    logger.warning(f"Invalid distractors format: {quiz_question_data.distractors}. Converting to empty list.")
                    quiz_question_data.distractors = []
                    
                logger.debug(f"Processed distractors: {quiz_question_data.distractors}")
                
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
                
                # Get the ID before the session is closed
                quiz_id = new_quiz.id
                logger.info(f"Successfully added quiz question for file {file_id}, id={quiz_id}")
                
                # Return the quiz question ID instead of the ORM object
                return quiz_id
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error adding quiz question: {e}", exc_info=True)
                return None

    def count_quiz_questions_for_file(self, file_id: int) -> int:
        """Count the total number of quiz questions for a file.
        
        Args:
            file_id: The ID of the file to count quiz questions for
            
        Returns:
            int: The total number of quiz questions for the file
        """
        with self.get_unit_of_work() as uow:
            try:
                # Count quiz questions for the file
                count = uow.session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                ).count()
                
                logger.info(f"[DEBUG] Counted {count} quiz questions for file {file_id}")
                return count
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to count quiz questions for file {file_id}: {e}", exc_info=True)
                return 0
    
    def get_quiz_questions_for_file(self, file_id: int, page: int = 1, page_size: int = 10) -> List[QuizQuestionResponse]:
        """Get quiz questions for a file by ID, only if it has been processed.
        
        Args:
            file_id: The ID of the file to get quiz questions for
            page: Page number (1-based)
            page_size: Number of items per page
            
        Returns:
            List of QuizQuestionResponse objects
        """
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
                # Then get quiz questions for this file with pagination
                query = uow.session.query(QuizQuestionORM).filter(
                    QuizQuestionORM.file_id == file_id
                )
                
                # Apply pagination
                offset = (page - 1) * page_size
                quiz_questions_orm = query.offset(offset).limit(page_size).all()
                
                logger.info(f"[DEBUG] Raw quiz questions query result: {quiz_questions_orm}")
                logger.info(f"[DEBUG] Found {len(quiz_questions_orm)} quiz questions for file {file_id} (page {page}, size {page_size})")
                
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

    def update_quiz_question(self, quiz_question_id: int, user_id: int, update_data: QuizQuestionUpdate) -> Optional[Dict[str, Any]]:
        """Update a quiz question's details from a Pydantic model, ensuring user ownership.
        
        Returns:
            Dictionary with the updated quiz question data or None if not found
        """
        logger.info(f"[DEBUG] Updating quiz question {quiz_question_id} by user {user_id}")
        with self.get_unit_of_work() as uow:
            try:
                # Get the quiz question with file relationship loaded
                quiz_question_orm = uow.session.query(QuizQuestionORM).join(File).filter(
                    QuizQuestionORM.id == quiz_question_id,
                    File.user_id == user_id
                ).first()

                if not quiz_question_orm:
                    logger.warning(f"Update failed: QuizQuestion {quiz_question_id} not found or user {user_id} lacks ownership.")
                    return None
                
                # Log the update data for debugging
                logger.debug(f"[DEBUG] Updating quiz question with data: {update_data.dict(exclude_unset=True)}")

                # Update the quiz question with the new data
                update_dict = update_data.dict(exclude_unset=True)
                for key, value in update_dict.items():
                    if hasattr(quiz_question_orm, key):
                        setattr(quiz_question_orm, key, value)
                
                # Update the updated_at timestamp
                quiz_question_orm.updated_at = datetime.utcnow()

                # Save changes
                uow.session.add(quiz_question_orm)
                uow.session.commit()
                uow.session.refresh(quiz_question_orm)
                
                logger.info(f"Successfully updated quiz question {quiz_question_id} by user {user_id}")
                
                # Convert to dictionary before the session is closed
                result = {
                    'id': quiz_question_orm.id,
                    'file_id': quiz_question_orm.file_id,
                    'key_concept_id': quiz_question_orm.key_concept_id,
                    'question': quiz_question_orm.question,
                    'question_type': quiz_question_orm.question_type,
                    'correct_answer': quiz_question_orm.correct_answer,
                    'distractors': quiz_question_orm.distractors or [],
                    'explanation': getattr(quiz_question_orm, 'explanation', None),
                    'is_custom': getattr(quiz_question_orm, 'is_custom', True),
                    'created_at': quiz_question_orm.created_at,
                    'updated_at': quiz_question_orm.updated_at
                }
                
                return result
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error updating quiz question {quiz_question_id}: {e}", exc_info=True)
                return None

    def delete_quiz_question(self, quiz_question_id: int, user_id: int) -> bool:
        """Delete a quiz question by its ID, ensuring user ownership."""
        logger.info(f"[DEBUG] Deleting quiz question {quiz_question_id} by user {user_id}")
        with self.get_unit_of_work() as uow:
            try:
                # Get the quiz question with file relationship loaded
                quiz_question_orm = uow.session.query(QuizQuestionORM).join(File).filter(
                    QuizQuestionORM.id == quiz_question_id,
                    File.user_id == user_id
                ).first()

                if not quiz_question_orm:
                    logger.warning(f"Delete failed: QuizQuestion {quiz_question_id} not found or user {user_id} lacks ownership.")
                    return False
                
                # Log the quiz question details for debugging
                logger.debug(f"[DEBUG] Deleting quiz question: {quiz_question_orm}")

                # Delete the quiz question
                uow.session.delete(quiz_question_orm)
                uow.session.commit()
                
                logger.info(f"Successfully deleted quiz question {quiz_question_id} by user {user_id}")
                return True
                
            except Exception as e:
                uow.session.rollback()
                logger.error(f"Error deleting quiz question {quiz_question_id}: {e}", exc_info=True)
                return False