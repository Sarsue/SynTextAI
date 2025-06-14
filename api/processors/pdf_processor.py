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

import fitz  # PyMuPDF
import numpy as np
import re

# Use absolute imports instead of relative imports
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.utils import chunk_text
from api.llm_service import get_text_embeddings_in_batches, generate_key_concepts_dspy
from api.processors.processor_utils import generate_learning_materials_for_concept, log_concept_processing_summary

# Import PDF extraction tools
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.converter import TextConverter

logger = logging.getLogger(__name__)

class PDFProcessor(FileProcessor):
    """
    Processor for PDF documents.
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
        processed_data = await self.process_pages(page_data)
        logger.info(f"Completed processing pages: generated {len(processed_data.get('chunks', []))} chunks")
        
        # Update the database with chunks and embeddings
        if processed_data and "chunks" in processed_data:
            logger.info(f"Storing {len(processed_data['chunks'])} chunks in database for file {file_id}")
            # Now properly awaiting the async method
            success = await self.store.update_file_with_chunks(
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
            content = " ".join([chunk.get("content", "") for chunk in processed_data["chunks"] 
                               if isinstance(chunk, dict) and "content" in chunk])
            
            try:
                # Generate key concepts using dspy - it now handles both chunking and format standardization
                key_concepts = generate_key_concepts_dspy(content, language, comprehension_level)
                
                # Add detailed logging of all concepts
                if key_concepts and isinstance(key_concepts, list):
                    logger.info(f"Extracted {len(key_concepts)} key concepts from document")
                    for i, concept in enumerate(key_concepts):
                        title = concept.get('concept_title', 'MISSING')
                        explanation = concept.get('concept_explanation', 'MISSING')
                        logger.debug(f"Raw concept {i+1}: title='{title[:100]}', explanation='{explanation[:100]}...'")
                else:
                    logger.warning(f"No key concepts were extracted from the document content")
                    return {
                        "success": False,
                        "file_id": file_id,
                        "error": "Failed to extract key concepts",
                        "metadata": {
                            "processor_type": "pdf",
                            "page_count": len(page_data)
                        }
                    }
                    
                if key_concepts and isinstance(key_concepts, list):
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
                        concept_id = await self.store.add_key_concept_async(
                            file_id=file_id,
                            concept_title=concept.get("concept_title", ""),
                            concept_explanation=concept.get("concept_explanation", ""),
                            source_page_number=concept.get("source_page_number"),
                            source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                            source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds")
                        )
                        
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
                # We continue even if key concept generation fails
        
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
        Process PDF pages: chunk text and generate embeddings.
        
        Args:
            page_data: List of dictionaries containing page numbers and content
            
        Returns:
            Dictionary with processed chunks including embeddings
        """
        all_chunks = []
        
        for page_item in page_data:
            try:
                page_content = page_item['content']
                page_num = page_item['page_num']
                
                if not page_content:
                    continue
                    
                # Chunk the page content
                text_chunks = chunk_text(page_content)
                non_empty_chunks = [chunk['content'] for chunk in text_chunks if chunk['content'].strip()]
                
                if not non_empty_chunks:
                    continue
                    
                # Generate embeddings for all non-empty chunks in batch
                chunk_embeddings = get_text_embeddings_in_batches(non_empty_chunks)
                
                # Ensure we got the same number of embeddings as chunks
                if len(chunk_embeddings) != len(non_empty_chunks):
                    logger.error(f"Mismatch between chunk count ({len(non_empty_chunks)}) "
                                f"and embedding count ({len(chunk_embeddings)}) for page {page_num}")
                    continue
                    
                # Create structured chunks with embeddings
                for i, chunk_content in enumerate(non_empty_chunks):
                    all_chunks.append({
                        'embedding': chunk_embeddings[i],
                        'content': chunk_content,
                        'metadata': {
                            'page': page_num,
                            'source_type': 'pdf'
                        }
                    })
                    
            except Exception as e:
                logger.error(f"Error processing page {page_item.get('page_num', 'unknown')}: {e}")
        
        return {"chunks": all_chunks}
    
    def extract_text_with_page_numbers(self, pdf_data: bytes) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF data while capturing page numbers.
        
        Args:
            pdf_data: PDF file data in bytes
            
        Returns:
            List of dictionaries with page numbers and text content
        """
        laparams = LAParams()
        page_texts = []
        
        try:
            # Use BytesIO to treat the PDF data as a file-like object
            with BytesIO(pdf_data) as file:
                resource_manager = PDFResourceManager()
                output = BytesIO()
                device = TextConverter(resource_manager, output, laparams=laparams)
                interpreter = PDFPageInterpreter(resource_manager, device)
                
                for page_num, page in enumerate(PDFPage.get_pages(file), 1):
                    output.seek(0)
                    output.truncate()
                    interpreter.process_page(page)
                    text = output.getvalue().decode("utf-8")
                    page_texts.append({
                        "page_num": page_num, 
                        "content": text
                    })
                    
            return page_texts
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
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
            
        # Extract text from all chunks for key concept generation
        chunks = content.get("chunks", [])
        full_text = " ".join([chunk.get("content", "") for chunk in chunks if chunk.get("content")])
        
        if not full_text.strip():
            logger.warning(f"No content available for key concept generation for file ID {file_id}")
            return []
            
        try:
            key_concepts = generate_key_concepts_dspy(document_text=full_text)
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
