"""
Factory for selecting the appropriate file processor.
"""
import os
import logging
from typing import Optional, Dict, Any, Type

from .base_processor import FileProcessor
from .youtube_processor import YouTubeProcessor
from .pdf_processor import PDFProcessor
from ..repositories.repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

class FileProcessingFactory:
    """
    Factory class that determines and returns the appropriate file processor based on file type.
    """
    
    def __init__(self, store: RepositoryManager):
        """
        Initialize the factory.
        
        Args:
            store: Repository manager instance for database operations
        """
        self.store = store
        
    def get_processor(self, filename: str) -> Optional[FileProcessor]:
        """
        Get the appropriate processor for the file.
        
        Args:
            filename: Name or URL of the file to process
            
        Returns:
            An instance of the appropriate FileProcessor subclass, or None if no suitable processor found
        """
        # Handle YouTube links
        if filename and isinstance(filename, str) and filename.startswith('http'):
            if 'youtube.com' in filename or 'youtu.be' in filename:
                logger.info(f"Selected YouTubeProcessor for: {filename}")
                return YouTubeProcessor(self.store)
            else:
                logger.info(f"URL detected but not YouTube: {filename}")
                # Could add other URL-based processors here in the future
        
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
