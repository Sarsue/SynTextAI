"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
import os
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO
import tempfile
import base64
import json
from datetime import datetime
import hashlib

import pytesseract
from PIL import Image
import io
import fitz  # PyMuPDF
from api.repositories.repository_manager import RepositoryManager
from api.processors.base_processor import FileProcessor
from api.llm_service import _deduplicate_concepts, _validate_references, _standardize_concept_format,get_text_embeddings_in_batches, generate_key_concepts
from api.processors.processor_utils import generate_learning_materials_for_concept, log_concept_processing_summary
from api.schemas.learning_content import KeyConceptCreate
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.converter import TextConverter
from api.utils import chunk_text
logger = logging.getLogger(__name__)

class PDFProcessor(FileProcessor):
    """
    Processor for PDF documents.
{{ ... }}
    Handles text extraction, embedding generation, and key concept extraction.
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
        processed_data = await self.process_pages(page_data)
        logger.info(f"Completed processing pages: generated {len(processed_data.get('chunks', []))} chunks")
        
        # Update the database with chunks and embeddings
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
                
            # Generate key concepts
            try:
                await self.store.file_repo.update_file_status(int(file_id), "generating_concepts")
            except Exception:
                logger.debug("Non-fatal: could not update status to 'generating_concepts'")
            content = " ".join([chunk.get("content", "") for chunk in processed_data["chunks"] 
                               if isinstance(chunk, dict) and "content" in chunk])
            
            try:
                # Generate key concepts using Mistral
                key_concepts = generate_key_concepts(content, language, comprehension_level)
                
                logger.info(f"generate_key_concepts returned: {type(key_concepts)}, length: {len(key_concepts) if key_concepts else 0}")
                
                # Add detailed logging of all concepts
                if key_concepts and isinstance(key_concepts, list) and len(key_concepts) > 0:
                    logger.info(f"Extracted {len(key_concepts)} key concepts from document")
                    for i, concept in enumerate(key_concepts):
                        title = concept.get('concept_title', 'MISSING')
                        explanation = concept.get('concept_explanation', 'MISSING')
                        logger.debug(f"Raw concept {i+1}: title='{title[:100]}', explanation='{explanation[:100]}...'")
                else:
                    logger.warning(f"No key concepts were extracted from the document content. key_concepts type: {type(key_concepts)}, value: {key_concepts}")
                    return {
                        "success": False,
                        "file_id": file_id,
                        "error": "Failed to extract key concepts",
                        "metadata": {
                            "processor_type": "pdf",
                            "page_count": len(page_data)
                        }
                    }
                    
                if key_concepts and isinstance(key_concepts, list) and len(key_concepts) > 0:
                    logger.info(f"Processing {len(key_concepts)} key concepts for file {file_id}")
                    concepts_processed = 0
                    
                    # Process one concept at a time - save concept, then generate and save its learning materials
                    for i, concept in enumerate(key_concepts):
                        title = concept.get("concept_title", "")
                        explanation = concept.get("concept_explanation", "")
                        logger.info(f"Processing concept {i+1}/{len(key_concepts)}: '{title[:50]}...'")
                        logger.debug(f"Concept details - title: '{title}', explanation: '{explanation[:100]}...'")
                        
                        # 1. Save the concept to get its ID
                        logger.debug(f"Attempting to save concept to database: '{title[:50]}...'")
                        key_concept_create = KeyConceptCreate(
                            concept_title=concept.get("concept_title", ""),
                            concept_explanation=concept.get("concept_explanation", ""),
                            source_page_number=concept.get("source_page_number"),
                            source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                            source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds"),
                            is_custom=False
                        )
                        concept_result = await self.store.learning_material_repo.add_key_concept(
                            file_id=file_id,
                            key_concept_data=key_concept_create
                        )
                        concept_id = concept_result.get('id') if concept_result else None
                        
                        if concept_id is not None:
                            logger.info(f"Saved concept '{title[:30]}...' with ID: {concept_id}")
                            logger.debug(f"Successfully saved concept with ID {concept_id} to database")
                            
                            # 2. Generate and save learning materials for this concept
                            concept_with_id = {
                                "concept_title": concept.get("concept_title", ""), 
                                "concept_explanation": concept.get("concept_explanation", ""),
                                "id": concept_id
                            }
                            
                            logger.debug(f"Preparing to generate learning materials for concept ID {concept_id}")
                            logger.debug(f"Concept with ID data: {concept_with_id}")
                            
                            # Generate flashcards and quizzes immediately for this concept
                            result = await self.generate_learning_materials_for_concept(file_id, concept_with_id)
                            
                            if result:
                                concepts_processed += 1
                                logger.info(f"Successfully generated learning materials for concept '{title[:30]}...'")
                            else:
                                logger.error(f"Failed to generate learning materials for concept '{title[:30]}...'")
                        else:
                            logger.error(f"Failed to save concept '{title[:30]}...' to database for file {file_id}")
                    
                    logger.info(f"Completed processing {concepts_processed}/{len(key_concepts)} key concepts for file {file_id}")

                else:
                    logger.warning(f"No valid key concepts generated for file {file_id}")
            except Exception as e:
                logger.error(f"Error generating key concepts: {e}")
                # We cant continue  if key concept generation fails
                return {
                        "success": False,
                        "file_id": file_id,
                        "error": "Failed to extract key concepts",
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
                "key_concepts_count": len(key_concepts) if 'key_concepts' in locals() and key_concepts else 0,
                "processor_type": "pdf"
            }
        }
    
    async def process_pages(self, page_data: List[Dict]) -> Dict[str, Any]:
        """
        Process PDF pages: chunk text and generate embeddings efficiently.
        Collects all chunks first, then processes in large batches to avoid rate limits.
        """
        all_chunks = []

        # First pass: extract and chunk all pages
        for page_item in page_data:
            try:
                page_content = page_item['content']
                page_num = page_item['page_num']

                if not page_content:
                    continue

                # Chunk the page content
                text_chunks = chunk_text(page_content)
                non_empty_chunks = [chunk['content'] for chunk in text_chunks if chunk['content'].strip()]

                # Collect chunks with metadata for later processing
                for chunk_content in non_empty_chunks:
                    all_chunks.append({
                        'text': chunk_content,  # Use 'text' for ChunkORM
                        'page_num': page_num,
                        'source_type': 'pdf'
                    })

            except Exception as e:
                logger.error(f"Error processing page {page_item.get('page_num', 'unknown')}: {e}")

        # Single large batch for all embeddings
        if all_chunks:
            chunk_texts = [chunk['text'] for chunk in all_chunks]
            chunk_embeddings = get_text_embeddings_in_batches(chunk_texts, batch_size=100)

            # Combine chunks with their embeddings
            for i, chunk in enumerate(all_chunks):
                chunk['embedding'] = chunk_embeddings[i] if i < len(chunk_embeddings) else None
                chunk['metadata'] = {
                    'page': chunk['page_num'],
                    'source_type': 'pdf'
                }

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
                    text = page.get_text("text")
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
    
    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the PDF content.
        
        Args:
            content: Processed content with chunks
            **kwargs: Additional parameters including file_id
            
        Returns:
            List of key concepts
        """
        file_id = kwargs.get('file_id')
        if not file_id:
            raise ValueError("Missing required 'file_id' parameter")
            
        # Extract key concepts from full document to reduce LLM calls
        pages = content.get("pages", [])
        full_text = " ".join([p.get("text", "") for p in pages])  # Use 'text' for consistency
        if not full_text.strip():
            logger.warning(f"No content available for key concept generation for file ID {file_id}")
            return []
        
        try:
            language = kwargs.get('language', 'English')
            comprehension_level = kwargs.get('comprehension_level', 'Beginner')
            key_concepts = generate_key_concepts(document_text=full_text, language=language, comprehension_level=comprehension_level, is_video=False)
            # Assign page numbers based on content distribution (simple heuristic)
            total_pages = len(pages)
            for concept in key_concepts:
                concept["source_page_number"] = 1  # Default to page 1, can improve later
            return key_concepts
        except Exception as e:
            self._log_error(f"Error generating key concepts for file {file_id}", e)
            return []
    
    async def generate_learning_materials(self, file_id: int, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate flashcards, MCQs, and T/F questions from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts that have already been stored in the database with their IDs
            
        Returns:
            Dict: Summary of concept processing results
        """
        if not key_concepts:
            logger.warning(f"No key concepts provided to generate learning materials for file {file_id}")
            return {"concepts_processed": 0, "concepts_successful": 0, "concepts_failed": 0}
            
        logger.info(f"Processing {len(key_concepts)} key concepts for file {file_id}")
        
        # Process each concept and track success/failure
        concept_results = []
        for concept in key_concepts:
            result = await self.generate_learning_materials_for_concept(file_id, concept)
            concept_results.append(result)
            
        # Use the shared utility to log summary and return results
        return await log_concept_processing_summary(concept_results, file_id)
    
    async def generate_learning_materials_for_concept(self, file_id: int, concept: Dict[str, Any]) -> bool:
        """
        Generate and save learning materials for a single key concept.
        Delegates to the shared utility function.
        
        Args:
            file_id: ID of the file
            concept: A single key concept with ID
            
        Returns:
            bool: Success status
        """
        # Use the shared utility function and pass in our store
        return await generate_learning_materials_for_concept(self.store, file_id, concept)

    def _log_error(self, message: str, error: Exception) -> None:
        """Log an error with consistent format."""
        logging.error(f"{message}: {str(error)[:200]}", exc_info=True)
