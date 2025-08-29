"""
Processors package for handling different file types in the RAG pipeline.
This package implements a modular approach to file processing following SOLID principles.
"""

from api.processors.factory import FileProcessingFactory, ProcessResult
from api.processors.pdf_processor import process_pdf
from api.processors.youtube_processor import process_youtube
from api.processors.text_processor import process_text
from api.processors.url_processor import process_url

__all__ = [
    'FileProcessingFactory',
    'ProcessResult',
    'process_pdf',
    'process_youtube',
    'process_text',
    'process_url'
]
