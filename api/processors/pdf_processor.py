"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
import os
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO

# Use absolute imports instead of relative imports
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.utils import chunk_text
from api.llm_service import get_text_embeddings_in_batches, generate_key_concepts_dspy

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
                # Generate key concepts using dspy or alternative method (synchronous call)
                key_concepts = generate_key_concepts_dspy(content, language, comprehension_level)
                
                if key_concepts and isinstance(key_concepts, list):
                    # Add key concepts to database
                    for concept in key_concepts:
                        # Not using await since add_key_concept is a synchronous method
                        self.store.add_key_concept(
                            file_id=file_id,
                            concept_title=concept.get("concept_title", ""),
                            concept_explanation=concept.get("concept_explanation", ""),
                            source_page_number=concept.get("source_page_number"),
                            source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                            source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds")
                        )
                    logger.info(f"Added {len(key_concepts)} key concepts for file {file_id}")
                    
                    # Generate learning materials (flashcards, MCQs, true/false questions)
                    if key_concepts:
                        logger.info(f"Generating learning materials for file_id {file_id}")
                        await self.generate_learning_materials(file_id, key_concepts)
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
    
    async def generate_learning_materials(self, file_id: str, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate flashcards, MCQs, and T/F questions from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts
            
        Returns:
            Dict with counts of generated materials
        """
        try:
            # Import needed functions from tasks module
            from ..tasks import (
                generate_flashcards_from_key_concepts,
                generate_mcq_from_key_concepts,
                generate_true_false_from_key_concepts
            )
        except ImportError as e:
            logger.error(f"Error importing learning material generators: {e}")
            return {"flashcards": 0, "mcqs": 0, "true_false": 0}
        
        if not key_concepts:
            logger.warning(f"No key concepts available to generate learning materials for file_id: {file_id}")
            return {"flashcards": 0, "mcqs": 0, "true_false": 0}
        
        results = {"flashcards": 0, "mcqs": 0, "true_false": 0}
        
        # Convert file_id to integer as repository methods expect int
        try:
            file_id_int = int(file_id)
        except ValueError:
            logger.error(f"Invalid file_id: {file_id}, cannot convert to integer")
            return results
        
        # Generate flashcards with timeout
        try:
            flashcards = await asyncio.wait_for(
                generate_flashcards_from_key_concepts(key_concepts), 
                timeout=180  # 3 minutes timeout
            )
            
            if flashcards:
                logger.info(f"Generated {len(flashcards)} flashcards from key concepts")
                for card in flashcards:
                    # Not using await since add_flashcard is a synchronous method
                    self.store.add_flashcard(
                        file_id=file_id_int,
                        question=card.get('front', ''),
                        answer=card.get('back', ''),
                        key_concept_id=None,
                        is_custom=False
                    )
                results["flashcards"] = len(flashcards)
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"Error generating flashcards: {e}")
        
        # Generate MCQs with timeout
        try:
            mcqs = await asyncio.wait_for(
                generate_mcq_from_key_concepts(key_concepts),
                timeout=180  # 3 minutes timeout
            )
            
            if mcqs:
                logger.info(f"Generated {len(mcqs)} MCQs from key concepts")
                for mcq in mcqs:
                    # Extract the correct answer and distractors from options
                    options = mcq.get('options', [])
                    answer = mcq.get('answer', '')
                    
                    # Not using await since add_quiz_question is a synchronous method
                    self.store.add_quiz_question(
                        file_id=file_id_int,
                        question=mcq.get('question', ''),
                        question_type="MCQ",
                        correct_answer=answer,
                        distractors=[opt for opt in options if opt != answer],
                        key_concept_id=None,
                        is_custom=False
                    )
                results["mcqs"] = len(mcqs)
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"Error generating MCQs: {e}")
        
        # Generate True/False questions with timeout
        try:
            tf_questions = await asyncio.wait_for(
                generate_true_false_from_key_concepts(key_concepts),
                timeout=180  # 3 minutes timeout
            )
            
            if tf_questions:
                logger.info(f"Generated {len(tf_questions)} True/False questions from key concepts")
                for tf in tf_questions:
                    # Create a properly formatted True/False question
                    # Not using await since add_quiz_question is a synchronous method
                    self.store.add_quiz_question(
                        file_id=file_id_int,
                        question=tf.get('statement', ''),
                        question_type="TF",
                        correct_answer="True" if tf.get('is_true', False) else "False",
                        distractors=[],  # T/F questions don't need additional distractors
                        key_concept_id=None,
                        is_custom=False
                    )
                results["true_false"] = len(tf_questions)
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"Error generating True/False questions: {e}")
        
        return results

    def _log_error(self, message: str, error: Exception) -> None:
        """Log an error with consistent format."""
        logging.error(f"{message}: {str(error)[:200]}", exc_info=True)
