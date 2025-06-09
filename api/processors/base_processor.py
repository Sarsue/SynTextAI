"""
Base processor classes and interfaces for file processing in the RAG pipeline.
These provide the foundation for all specific file type processors.
"""
from abc import ABC, abstractmethod
import logging
from typing import Dict, List, Any, Optional, Tuple

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
        pass
    
    @abstractmethod
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the file.
        
        Returns:
            Dict containing extracted content
        """
        pass
        
    @abstractmethod
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted content.
        
        Args:
            content: Extracted content
            
        Returns:
            Dict containing content with embeddings
        """
        pass
        
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
        pass
        
    @abstractmethod
    async def generate_learning_materials(self, 
                                        file_id: str,
                                        key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate learning materials from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts
            
        Returns:
            Dict containing generated learning materials
        """
        pass
        
    def _log_error(self, message: str, error: Exception, exc_info: bool = True) -> None:
        """Utility method for consistent error logging."""
        logger.error(f"{message}: {error}", exc_info=exc_info)
