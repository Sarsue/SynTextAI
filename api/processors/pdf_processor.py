"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
import os
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO

from .base_processor import FileProcessor
from ..repositories.repository_manager import RepositoryManager
from ..utils import chunk_text
from ..llm_service import get_text_embeddings_in_batches, generate_key_concepts_dspy

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
        logger.info(f"Processing PDF file: {filename} (ID: {file_id}, User: {user_id})")
        
        # Extract text from PDF with page numbers
        page_data = self.extract_text_with_page_numbers(file_data)
        logger.info(f"PDF extraction complete. Pages: {len(page_data)}")
        
        if not page_data:
            logger.error(f"Failed to extract content from PDF: {filename}")
            return {"success": False, "error": "Failed to extract content from PDF"}
            
        # Process extracted pages and generate embeddings
        processed_data = await self.process_pages(page_data)
        
        # Update the database with chunks and embeddings
        if processed_data and "chunks" in processed_data:
            success = await self.store.add_chunks_for_file(
                file_id=file_id,
                chunks=processed_data["chunks"]
            )
            
            if not success:
                logger.error(f"Failed to store chunks for file {file_id}")
                return {"success": False, "error": "Failed to store chunks"}
                
            # Generate key concepts
            content = " ".join([chunk.get("content", "") for chunk in processed_data["chunks"] 
                               if isinstance(chunk, dict) and "content" in chunk])
            
            try:
                # Generate key concepts using dspy or alternative method
                key_concepts = await generate_key_concepts_dspy(content)
                
                if key_concepts and isinstance(key_concepts, list):
                    # Add key concepts to database
                    for concept in key_concepts:
                        await self.store.add_key_concept(
                            file_id=file_id,
                            concept=concept.get("concept", ""),
                            explanation=concept.get("explanation", ""),
                            span_text=concept.get("span_text", ""),
                            span_start=concept.get("span_start", 0),
                            span_end=concept.get("span_end", 0)
                        )
                    logger.info(f"Added {len(key_concepts)} key concepts for file {file_id}")
                else:
                    logger.warning(f"No valid key concepts generated for file {file_id}")
            except Exception as e:
                logger.error(f"Error generating key concepts: {e}")
                # We continue even if key concept generation fails
        
        return {
            "success": True,
            "page_count": len(page_data),
            "chunk_count": len(processed_data.get("chunks", [])) if processed_data else 0
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
