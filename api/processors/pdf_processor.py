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
        processed_data = await self.process_pages(page_data, file_name=filename)
        logger.info(f"Completed processing pages: generated {len(processed_data.get('chunks', []))} chunks")
        
        # Update the database with chunks and embeddings
        # A document that extracted nothing is a failure, not a success. Every
        # per-item exception here is caught and logged so one bad section cannot
        # lose a whole document, which is right, but it meant a processor could
        # catch every section, produce zero chunks, and still mark the file
        # processed. The file then sits in the list looking ready and answers
        # nothing, and the only trace is a log line nobody reads.
        #
        # This exact shape shipped twice: once when the storage layer read a
        # structure no processor produced, and again on 2026-08-07 when
        # page_text was added referring to a variable that exists in the PDF
        # processor and not in this one. Both were silent.
        if processed_data is not None and not (processed_data.get("chunks") or []):
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

        if processed_data and "chunks" in processed_data:
            logger.info(f"Storing {len(processed_data['chunks'])} chunks in database for file {file_id}")
            # Update status to 'storing' before DB writes
            try:
                await self.store.file_repo.update_file_status(int(file_id), "storing")
            except Exception:
                logger.debug("Non-fatal: could not update status to 'storing'")
            # Now properly awaiting the async method
            success = await self.store.file_repo.update_file_with_chunks(
                user_id=user_id,
                filename=filename,
                file_type="pdf",
                extracted_data=processed_data["chunks"]
            )
            logger.info(f"Database update with chunks {'successful' if success else 'failed'} for file {file_id}")
            
            if not success:
                logger.error(f"Failed to store chunks for file {file_id}")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to store chunks",
                    "metadata": {
                        "processor_type": "pdf",
                        "page_count": len(page_data)
                    }
                }
                
        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "page_count": len(page_data),
                "chunk_count": len(processed_data.get("chunks", [])) if processed_data else 0,
                "processor_type": "pdf"
            }
        }
    
    async def process_pages(self, page_data: List[Dict], file_name: str = "") -> Dict[str, Any]:
        """
        Process PDF pages: chunk text and generate embeddings incrementally.
        Processes pages in batches to manage memory efficiently.
        PRODUCTION-READY: Handles 1000+ page PDFs without OOM.
        """
        all_chunks = []
        BATCH_SIZE = 50  # Process 50 pages at a time to manage memory
        total_pages = len(page_data)
        
        logger.info(f"Processing {total_pages} pages in batches of {BATCH_SIZE}")

        # Process pages in batches to avoid loading all chunks into memory
        for batch_start in range(0, total_pages, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_pages)
            page_batch = page_data[batch_start:batch_end]
            batch_chunks = []
            
            logger.info(f"Processing pages {batch_start+1}-{batch_end} of {total_pages}")

            # Extract and chunk this batch of pages
            for page_item in page_batch:
                try:
                    page_content = page_item['text']
                    page_num = page_item['page_num']

                    if not page_content:
                        continue

                    # Chunk the page content
                    text_chunks = chunk_text(page_content)
                    non_empty_chunks = [chunk['content'] for chunk in text_chunks if chunk['content'].strip()]

                    # Collect chunks for this batch
                    for chunk_content in non_empty_chunks:
                        batch_chunks.append({
                            'text': chunk_content,
                            # The whole page, carried alongside each of its
                            # chunks. Storage groups by page to build the
                            # citation unit, and joining the chunks back
                            # together would duplicate their overlap.
                            'page_text': page_content,
                            'page_num': page_num,
                            'source_type': 'pdf'
                        })

                except Exception as e:
                    logger.error(f"Error processing page {page_item.get('page_num', 'unknown')}: {e}")

            # Give each chunk a sentence of context before embedding it, so a
            # page is searchable by what it is about and not only by the words
            # that happen to be on it. No-op unless CONTEXTUALIZE_CHUNKS is on,
            # and never fatal: a document with no context is merely harder to
            # find, a document that failed to ingest is not there at all.
            if batch_chunks:
                try:
                    await add_context(batch_chunks, file_name)
                except Exception as ctx_error:
                    logger.warning(f"Contextualisation skipped: {ctx_error}")

            # Generate embeddings for this batch
            if batch_chunks:
                chunk_texts = [embedding_text(chunk) for chunk in batch_chunks]
                
                try:
                    logger.info(f"Generating embeddings for {len(chunk_texts)} chunks...")
                    chunk_embeddings = await get_text_embeddings_in_batches(chunk_texts, batch_size=50)
                except Exception as e:
                    logger.error(f"Embedding generation failed for batch: {e}")
                    raise ValueError(f"Failed to generate embeddings: {e}")
                
                # Validate embeddings
                if not chunk_embeddings or len(chunk_embeddings) != len(chunk_texts):
                    raise ValueError(f"Embedding count mismatch: expected {len(chunk_texts)}, got {len(chunk_embeddings)}")
                
                embedding_dim = len(chunk_embeddings[0]) if chunk_embeddings else 0
                if embedding_dim == 0:
                    raise ValueError("Embeddings have zero dimensions - model failed to generate valid vectors")
                
                logger.info(f"✅ Generated {len(chunk_embeddings)} embeddings with dimension {embedding_dim}")

                # Combine chunks with their embeddings
                for i, chunk in enumerate(batch_chunks):
                    chunk['embedding'] = chunk_embeddings[i] if i < len(chunk_embeddings) else None
                    chunk['metadata'] = {
                        'page': chunk['page_num'],
                        'source_type': 'pdf'
                    }
                
                all_chunks.extend(batch_chunks)
                
                # CRITICAL: Force garbage collection after each batch
                batch_chunks = None
                chunk_texts = None
                chunk_embeddings = None
                gc.collect()
                logger.debug(f"Memory cleaned after batch {batch_start//BATCH_SIZE + 1}")

        logger.info(f"✅ Completed processing all {total_pages} pages, generated {len(all_chunks)} total chunks")
        return {"chunks": all_chunks}
    
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
