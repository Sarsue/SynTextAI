"""
Processors package for handling different file types in the RAG pipeline.
This package implements a modular approach to file processing following SOLID principles.
"""

from .factory import get_processor, register_processor, ProcessResult
from .pdf_processor import process_pdf
from .youtube_processor import process_youtube
from .text_processor import process_text

__all__ = [
    'get_processor',
    'register_processor',
    'ProcessResult',
    'process_pdf',
    'process_youtube',
    'process_text'
]
