"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
from ..core.utils import sanitize_extracted_text
import os
import asyncio
import gc
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO
import json
from datetime import datetime

import pytesseract
from PIL import Image
import io
import fitz  # PyMuPDF
from api.repositories.repository_manager import RepositoryManager
from api.processors.base_processor import FileProcessor
from api.services.llm_service import get_text_embeddings_in_batches
from api.services.contextualizer import add_context, embedding_text
from api.services.outline import extract_pdf_outline
from api.core.utils import chunk_text
logger = logging.getLogger(__name__)

class PDFProcessor(FileProcessor):
    """
    Processor for PDF documents.
    Handles text extraction and embedding generation.
    """
    
    def __init__(self, store: RepositoryManager):
        """
        Initialize the PDF processor.
        
        Args:
            store: RepositoryManager instance for database operations
        """
        super().__init__()
        self.store = store
        
    async def process(self, 
                     file_data: bytes, 
                     file_id: int, 
                     user_id: int, 
                     filename: str, 
                     **kwargs) -> Dict[str, Any]:
        """
        Process a PDF file: extract text, generate embeddings, create key concepts.
        
        Args:
            file_data: Raw PDF file data in bytes
            file_id: Database ID of the file
            user_id: ID of the user who owns the file
            filename: Name of the file
            **kwargs: Additional arguments
            
        Returns:
            Dictionary containing processing results
        """
        # Extract additional parameters
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        
        logger.info(f"Processing PDF file: {filename} (ID: {file_id}, User: {user_id}, Language: {language}, Level: {comprehension_level})")
        
        # Extract text from PDF with page numbers
        page_data = self.extract_text_with_page_numbers(file_data)
        logger.info(f"PDF extraction complete. Pages: {len(page_data)}")

        # And what the document says it contains. Cheap, one call on a document
        # already being opened, and the only thing that lets an answer cite the
        # section that defines a rule rather than a page that mentions it.
        try:
            outline = extract_pdf_outline(file_data)
            if outline:
                await self.store.file_repo.set_outline(int(file_id), outline)
        except Exception as outline_error:
            logger.warning(f"Outline extraction skipped for {filename}: {outline_error}")
        
        if not page_data:
            logger.error(f"Failed to extract content from PDF: {filename}")
            return {
                "success": False,
                "file_id": file_id,
                "error": "Failed to extract content from PDF",
                "metadata": {
                    "processor_type": "pdf"
                }
            }
            
        # Process extracted pages and generate embeddings
        logger.info(f"Starting page processing and embedding generation for file {filename}")
        # Update status to 'embedding' to support REST polling progress
        try:
            await self.store.file_repo.update_file_status(int(file_id), "embedding")
        except Exception:
            logger.debug("Non-fatal: could not update status to 'embedding'")

        # Chunks are written batch by batch inside this call, so a crash keeps
        # the pages it already reached and the answer can already cite them.
        result = await self.embed_and_store_pages(
            page_data,
            file_id=int(file_id),
            user_id=user_id,
            filename=filename,
            file_type="pdf",
        )

        # A document that extracted nothing is a failure, not a success. Every
        # per-item exception is caught and logged so one bad page cannot lose a
        # whole document, which is right, but it meant a processor could catch
        # every page, produce zero chunks, and still mark the file processed.
        # The file then sits in the list looking ready and answers nothing, and
        # the only trace is a log line nobody reads.
        #
        # This exact shape shipped twice: once when the storage layer read a
        # structure no processor produced, and again on 2026-08-07 when
        # page_text was added referring to a variable that existed in one
        # processor and not another. Both were silent, and both are the reason
        # that loop now lives in one place.
        #
        # Counted across the whole document, not this attempt: a resumed run
        # that finds every page already stored does no work and is finished,
        # not empty.
        if result["stored_chunks"] == 0 and result["skipped_pages"] == 0:
            logger.error(
                f"No chunks extracted from {filename}; marking it failed rather "
                f"than leaving a document that looks ready and answers nothing"
            )
            try:
                await self.store.file_repo.update_file_status(int(file_id), "failed")
            except Exception:
                logger.debug("Non-fatal: could not update status to 'failed'")
            return {
                "success": False,
                "file_id": file_id,
                "error": "No content could be extracted from this document",
                "metadata": {"processor_type": "pdf"},
            }

        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "page_count": len(page_data),
                "chunk_count": result["stored_chunks"],
                "resumed_pages": result["skipped_pages"],
                "processor_type": "pdf"
            }
        }
    
    def extract_text_with_page_numbers(self, pdf_data: bytes) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF data while capturing page numbers.
        Falls back to OCR (Tesseract) if a page contains only images.
        
        Args:
            pdf_data: PDF file data in bytes

        Returns:
            List of dictionaries with page numbers and text content
        """
        page_texts = []
        try:
            with fitz.open(stream=pdf_data, filetype="pdf") as doc:
                for page_num, page in enumerate(doc, 1):
                    text = sanitize_extracted_text(page.get_text("text"))
                    if not text.strip():
                        # No extractable text -> use OCR
                        pix = page.get_pixmap(dpi=300)
                        img_bytes = pix.tobytes("png")
                        image = Image.open(io.BytesIO(img_bytes))
                        text = pytesseract.image_to_string(image)
                        logger.debug(f"OCR extracted: {len(text)} chars")

                    page_texts.append({
                        "page_num": page_num,
                        "text": f"Page {page_num}\n{text.strip()}"  # Use 'text' for consistency
                    })
                    
            return page_texts
        except Exception as e:
            logger.error(f"Error extracting text (Tesseract fallback): {e}", exc_info=True)
            return []
    
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the PDF file.
        
        Args:
            **kwargs: Must include 'file_data' as bytes
            
        Returns:
            Dict containing extracted page content
        """
        file_data = kwargs.get('file_data')
        if not file_data:
            raise ValueError("Missing required 'file_data' parameter")
            
        page_data = self.extract_text_with_page_numbers(file_data)
        return {"pages": page_data}
    
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted PDF content.
        
        Args:
            content: Dictionary containing pages with extracted content
            
        Returns:
            Dict containing content with embeddings
        """
        pages = content.get("pages", [])
        processed_data = await self.process_pages(pages)
        return processed_data
    
    def _log_error(self, message: str, error: Exception) -> None:
        """Log an error with consistent format."""
        logging.error(f"{message}: {str(error)[:200]}", exc_info=True)
