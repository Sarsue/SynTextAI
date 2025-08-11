"""
Factory for selecting the appropriate file processor.
"""
import os
import logging
from typing import Optional

# Use absolute imports instead of relative imports
from api.processors.base_processor import FileProcessor
from api.processors.youtube_processor import YouTubeProcessor
from api.processors.pdf_processor import PDFProcessor
from api.processors.url_processor import URLProcessor
from api.repositories.repository_manager import RepositoryManager
from api.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

class FileProcessingFactory:
    """
    Factory class that determines and returns the appropriate file processor based on file type.
    """
    
    def __init__(self, store: RepositoryManager, embedding_service: Optional[EmbeddingService] = None):
        """
        Initialize the factory.
        
        Args:
            store: Repository manager instance for database operations
            embedding_service: Optional embedding service instance
        """
        self.store = store
        self.embedding_service = embedding_service or EmbeddingService()
        
    def get_processor(self, filename: str) -> Optional[FileProcessor]:
        """
        Get the appropriate processor for the file.
        
        Args:
            filename: Name or URL of the file to process
            
        Returns:
            An instance of the appropriate FileProcessor subclass, or None if no suitable processor found
        """
        # Handle URLs
        if filename and isinstance(filename, str) and filename.startswith('http'):
            # First check for specific URL types with dedicated processors
            if 'youtube.com' in filename or 'youtu.be' in filename:
                logger.info(f"Selected YouTubeProcessor for: {filename}")
                return YouTubeProcessor(self.store)
            
            # Use the generic URL processor for all other URLs
            logger.info(f"Selected URLProcessor for: {filename}")
            return URLProcessor(embedding_service=self.embedding_service)
        
        # Handle files by extension
        try:
            _, ext = os.path.splitext(filename)
            ext = ext.lower().strip('.')
            logger.debug(f"Detected file extension: .{ext} for file: {filename}")
        except Exception as e:
            logger.error(f"Error extracting extension from filename '{filename}': {e}")
            return None
        
        # Map file extensions to processor types
        processor_map = {
            # PDF files
            'pdf': PDFProcessor,
            
            # Video files
            'mp4': None,  # TODO: Implement VideoProcessor
            'mov': None,
            'avi': None,
            'mkv': None,
            'webm': None,
            
            # Audio files
            'mp3': None,  # TODO: Implement AudioProcessor
            'wav': None,
            'm4a': None,
            
            # Text files
            'txt': None,  # TODO: Implement TextProcessor
            'md': None,
            
            # Document files
            'docx': None,  # TODO: Implement DocxProcessor
            'doc': None,
        }
        
        processor_class = processor_map.get(ext)
        
        if processor_class:
            try:
                logger.info(f"Selected {processor_class.__name__} for file: {filename} with extension .{ext}")
                return processor_class(self.store)
            except Exception as e:
                logger.error(f"Error instantiating {processor_class.__name__} for file '{filename}': {e}")
                return None
        else:
            logger.warning(f"No suitable processor found for file: {filename} with extension .{ext}")
            return None
