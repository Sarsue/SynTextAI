"""
Repository service for handling database operations related to processing results.
"""
import logging
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

class RepositoryService:
    """Service for handling database operations for processing results."""
    
    def __init__(self, store):
        """Initialize with a repository manager instance."""
        self.store = store
    
    async def save_processing_results(
        self,
        file_id: Union[str, int],
        user_id: Union[str, int],
        concepts: List[Dict[str, Any]],
        study_results: Dict[str, List[Dict[str, Any]]],
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Save processing results to the database.
        
        Args:
            file_id: ID of the file being processed
            user_id: ID of the user who owns the file
            concepts: List of concept dictionaries to save
            study_results: Dictionary containing flashcards and quizzes
            metadata: Additional metadata about the processing
            
        Returns:
            bool: True if save was successful, False otherwise
        """
        try:
            # Convert IDs to integers if they're strings
            file_id = int(file_id) if isinstance(file_id, str) else file_id
            user_id = int(user_id) if isinstance(user_id, str) else user_id
            
            # Save concepts
            saved_concepts = []
            for concept in concepts:
                concept_id = await self.store.concept_repo.create_concept(
                    file_id=file_id,
                    user_id=user_id,
                    concept_title=concept.get('concept_title', ''),
                    concept_explanation=concept.get('concept_explanation', ''),
                    source_page_number=concept.get('source_page_number'),
                    source_video_timestamp_start_seconds=concept.get('source_video_timestamp_start_seconds'),
                    source_video_timestamp_end_seconds=concept.get('source_video_timestamp_end_seconds'),
                    metadata=concept.get('metadata', {})
                )
                if concept_id:
                    saved_concepts.append({**concept, 'id': concept_id})
            
            # Save study materials (flashcards, quizzes, etc.)
            for material_type, items in study_results.items():
                for item in items:
                    if material_type == 'flashcards':
                        await self.store.flashcard_repo.create_flashcard(
                            concept_id=item.get('concept_id'),
                            front_text=item.get('front_text', ''),
                            back_text=item.get('back_text', ''),
                            metadata=item.get('metadata', {})
                        )
                    elif material_type == 'quizzes':
                        await self.store.quiz_repo.create_quiz(
                            concept_id=item.get('concept_id'),
                            question=item.get('question', ''),
                            options=item.get('options', []),
                            correct_answer=item.get('correct_answer', ''),
                            explanation=item.get('explanation', ''),
                            metadata=item.get('metadata', {})
                        )
            
            # Update file metadata
            await self.store.file_repo.update_file_metadata(
                file_id=file_id,
                metadata={
                    **metadata,
                    'processing_complete': True,
                    'concept_count': len(saved_concepts)
                }
            )
            
            # Commit the transaction
            if hasattr(self.store, 'session'):
                self.store.session.commit()
            elif hasattr(self.store, 'file_repo') and hasattr(self.store.file_repo, 'session'):
                self.store.file_repo.session.commit()
            
            logger.info(f"Successfully saved processing results for file {file_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error in save_processing_results: {str(e)}", exc_info=True)
            if hasattr(self.store, 'session'):
                self.store.session.rollback()
            elif hasattr(self.store, 'file_repo') and hasattr(self.store.file_repo, 'session'):
                self.store.file_repo.session.rollback()
            return False

# Create a singleton instance of the repository service
# Note: This needs to be initialized with a store instance before use
repository_service = None
