"""
Base processor classes and interfaces for file processing in the RAG pipeline.
These provide the foundation for all specific file type processors.
"""
from abc import ABC, abstractmethod
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class FileProcessor(ABC):
    """Abstract base class for all file processors."""
    
    @abstractmethod
    async def process(self, 
                     user_id: str, 
                     file_id: str, 
                     filename: str, 
                     file_url: str, 
                     **kwargs) -> Dict[str, Any]:
        """
        Process the file and return results.
        
        Args:
            user_id: User ID
            file_id: File ID
            filename: Name of the file
            file_url: URL to access the file
            **kwargs: Additional parameters specific to the processor
            
        Returns:
            Dict containing processing results
        """
    
    @abstractmethod
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the file.
        
        Returns:
            Dict containing extracted content
        """
        
    @abstractmethod
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted content.
        
        Args:
            content: Extracted content
            
        Returns:
            Dict containing content with embeddings
        """
        
    @abstractmethod
    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the content.
        
        Args:
            content: Processed content
            **kwargs: Additional parameters
            
        Returns:
            List of key concepts
        """
        
    async def generate_learning_materials(self, 
                                      file_id: str,
                                      key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Default implementation for generating learning materials from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts
            
        Returns:
            Dict containing generated learning materials
        """
        from api.processors.processor_utils import generate_learning_materials
        
        if not key_concepts:
            logger.warning(f"No key concepts provided to generate learning materials for file {file_id}")
            return {"concepts_processed": 0, "concepts_successful": 0, "concepts_failed": 0}

        logger.info(f"Processing {len(key_concepts)} key concepts for file {file_id}")
        
        try:
            result = await generate_learning_materials(
                store=self.store,
                file_id=int(file_id),
                key_concepts=key_concepts,
                generate_flashcards=True,
                generate_mcqs=True,
                generate_tf_questions=True
            )
            
            return {
                "concepts_processed": result.total_concepts,
                "concepts_successful": result.successful_concepts,
                "concepts_failed": result.failed_concepts,
                "total_flashcards": result.total_flashcards,
                "total_mcqs": result.total_mcqs,
                "total_tf_questions": result.total_tf_questions,
                "duration_seconds": result.duration
            }
            
        except Exception as e:
            logger.error(f"Error in generate_learning_materials: {e}", exc_info=True)
            return {
                "concepts_processed": len(key_concepts),
                "concepts_successful": 0,
                "concepts_failed": len(key_concepts),
                "error": str(e)
            }
        
    async def generate_learning_materials_for_concept(self, 
                                                    file_id: str,
                                                    concept: Dict[str, Any]) -> bool:
        """
        Default implementation for generating learning materials for a single key concept.
        
        Args:
            file_id: File ID
            concept: A single key concept
            
        Returns:
            bool: True if generation was successful, False otherwise
        """
        from api.processors.processor_utils import generate_learning_materials
        
        try:
            result = await generate_learning_materials(
                store=self.store,
                file_id=int(file_id),
                key_concepts=[concept],
                generate_flashcards=True,
                generate_mcqs=True,
                generate_tf_questions=True
            )
            return result.successful_concepts > 0
        except Exception as e:
            logger.error(f"Error generating learning materials: {e}", exc_info=True)
            return False
        
    def _log_error(self, message: str, error: Exception, exc_info: bool = True) -> None:
        """Utility method for consistent error logging."""
        logger.error(f"{message}: {error}", exc_info=exc_info)
