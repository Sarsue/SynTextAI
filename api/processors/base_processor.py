"""
Base processor module - Defines the abstract base class for all file processors.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypeVar, Generic, Type, TypedDict, Literal
import logging
from dataclasses import dataclass
from datetime import datetime

# Create a generic type variable
T = TypeVar('T')

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """
    A dataclass to standardize the return type for all processor operations.
    """
    success: bool
    content: Optional[Dict[str, Any]] = None
    key_concepts: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the ProcessResult to a dictionary."""
        return {
            'success': self.success,
            'content': self.content,
            'key_concepts': self.key_concepts,
            'metadata': self.metadata,
            'error': self.error,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


class FileProcessor(ABC, Generic[T]):
    """
    Abstract base class for file processors.
    Implements a simplified processing pipeline with error handling.
    """
    
    async def process(self, **kwargs) -> ProcessResult:
        """
        Process a file through the entire pipeline:
        1. Extract content
        2. Generate embeddings
        3. Extract key concepts
        
        Args:
            **kwargs: Must include file_data, file_id, user_id, and filename
            
        Returns:
            Dictionary containing processing results and status
        """
        result = {
            'success': False,
            'file_id': kwargs.get('file_id'),
            'user_id': kwargs.get('user_id'),
            'filename': kwargs.get('filename', ''),
            'error': None,
            'metadata': {}
        }
        
        try:
            # Step 1: Extract content
            content = await self.extract_content(**kwargs)
            if not content.get('success'):
                result['error'] = content.get('error', 'Failed to extract content')
                return result
                
            result.update(content)
            
            # Step 2: Generate embeddings
            content_with_embeddings = await self.generate_embeddings(content, **kwargs)
            if not content_with_embeddings.get('success'):
                result['error'] = content_with_embeddings.get('error', 'Failed to generate embeddings')
                return result
                
            result.update(content_with_embeddings)
            
            # Step 3: Extract key concepts
            key_concepts = await self.generate_key_concepts(content_with_embeddings, **kwargs)
            result['key_concepts'] = key_concepts
            
            # Mark as successful
            result['success'] = True
            
        except Exception as e:
            logger.error(f"Error processing file: {str(e)}", exc_info=True)
            result['error'] = f"Processing failed: {str(e)}"
            
        return result
    
    @abstractmethod
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract content from the file.
        
        Args:
            **kwargs: Must include file_data, file_id, user_id, and filename
            
        Returns:
            Dictionary containing extracted content and metadata
        """
        pass
        
    @abstractmethod
    async def generate_embeddings(self, content: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted content.
        
        Args:
            content: Dictionary containing extracted content
            
        Returns:
            Dictionary with content and embeddings
        """
        pass
        
    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the extracted content.
        Can be overridden by subclasses for custom behavior.
        
        Args:
            content: Dictionary containing extracted content and embeddings
            
        Returns:
            List of key concepts
        """
        return []
