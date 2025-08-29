"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
from typing import Dict, List, Any, Optional
from io import BytesIO

import fitz  # PyMuPDF

# Use absolute imports instead of relative imports
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from ..llm_compat import get_text_embeddings_in_batches_sync, generate_key_concepts_dspy_sync as generate_key_concepts_dspy
from api.processors.processor_utils import generate_learning_materials_for_concept

# Import PDF extraction tools
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.converter import TextConverter

logger = logging.getLogger(__name__)

# Global instance of RepositoryManager for the standalone function
_repo_manager: Optional[RepositoryManager] = None

def process_pdf(
    file_data: bytes,
    file_id: int,
    user_id: int,
    filename: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Standalone function to process a PDF file.
    
    This is a convenience function that creates a PDFProcessor instance and processes the file.
    
    Args:
        file_data: The binary content of the PDF file
        file_id: ID of the file in the database
        user_id: ID of the user who owns the file
        filename: Name of the file
        **kwargs: Additional keyword arguments to pass to the processor
        
    Returns:
        Dictionary containing processing results
    """
    global _repo_manager
    if _repo_manager is None:
        from api.repositories.repository_manager import get_repository_manager
        _repo_manager = get_repository_manager()
        
    processor = PDFProcessor(_repo_manager)
    return processor.process(file_data, file_id, user_id, filename, **kwargs)

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
        try:
            # Extract additional parameters
            language = kwargs.get('language', 'English')
            comprehension_level = kwargs.get('comprehension_level', 'Beginner')
            
            logger.info(f"Processing PDF file: {filename} (ID: {file_id}, User: {user_id}, "
                       f"Language: {language}, Level: {comprehension_level})")
            
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
                        "processor_type": "pdf",
                        "page_count": 0
                    }
                }
            
            # Process pages to generate embeddings
            processed_data = self.process_pages(page_data)
            
            # Generate key concepts
            content = " ".join([chunk.get("content", "") for chunk in processed_data.get("chunks", []) 
                               if isinstance(chunk, dict) and "content" in chunk])
            
            try:
                # Generate key concepts using dspy
                key_concepts = generate_key_concepts_dspy(content, language, comprehension_level)
                
                if not key_concepts or not isinstance(key_concepts, list):
                    logger.warning("No valid key concepts were extracted from the document content")
                    return {
                        "success": False,
                        "file_id": file_id,
                        "error": "Failed to extract key concepts",
                        "metadata": {
                            "processor_type": "pdf",
                            "page_count": len(page_data)
                        }
                    }
                
                logger.info(f"Extracted {len(key_concepts)} key concepts from document")
                concepts_processed = 0
                
                # Process one concept at a time
                for i, concept in enumerate(key_concepts):
                    title = concept.get("concept_title", "")
                    explanation = concept.get("concept_explanation", "")
                    logger.info(f"Processing concept {i+1}/{len(key_concepts)}: '{title[:50]}...'")
                    
                    # Save the concept to get its ID
                    concept_id = await self.store.add_key_concept(
                        file_id=file_id,
                        concept_title=title,
                        concept_explanation=explanation,
                        source_page_number=concept.get("source_page_number"),
                        source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                        source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds")
                    )
                    
                    if concept_id is not None:
                        logger.info(f"Saved concept '{title[:30]}...' with ID: {concept_id}")
                        
                        # Generate and save learning materials for this concept
                        concept_with_id = {
                            "concept_title": title,
                            "concept_explanation": explanation,
                            "id": concept_id
                        }
                        
                        # Generate flashcards and quizzes for this concept
                        result = await self.generate_learning_materials_for_concept(file_id, concept_with_id)
                        
                        if result:
                            concepts_processed += 1
                            logger.info(f"Successfully generated learning materials for concept '{title[:30]}...'")
                        else:
                            logger.error(f"Failed to generate learning materials for concept '{title[:30]}...'")
                    else:
                        logger.error(f"Failed to save concept '{title[:30]}...' to database for file {file_id}")
                
                logger.info(f"Completed processing {concepts_processed}/{len(key_concepts)} key concepts for file {file_id}")
                
                return {
                    "success": True,
                    "file_id": file_id,
                    "page_count": len(page_data),
                    "metadata": {
                        "processor_type": "pdf",
                        "page_count": len(page_data),
                        "chunk_count": len(processed_data.get("chunks", [])),
                        "key_concepts_count": concepts_processed
                    }
                }
                    
            except Exception as e:
                logger.error(f"Error generating key concepts: {e}", exc_info=True)
                return {
                    "success": True,  # Still consider it a success since we have the content
                    "file_id": file_id,
                    "page_count": len(page_data),
                    "warning": f"Key concept generation failed: {str(e)}",
                    "metadata": {
                        "processor_type": "pdf",
                        "page_count": len(page_data),
                        "chunk_count": len(processed_data.get("chunks", [])),
                        "key_concepts_count": 0
                    }
                }
                
        except Exception as e:
            logger.error(f"Error processing PDF file {filename}: {e}", exc_info=True)
            return {
                "success": False,
                "file_id": file_id,
                "error": str(e),
                "metadata": {
                    "processor_type": "pdf"
                }
            }
    
    def process_pages(self, page_data: List[Dict]) -> Dict[str, Any]:
        """
        Process PDF pages: chunk text and generate embeddings.
        
        Args:
            page_data: List of dictionaries containing page numbers and content
            
        Returns:
            Dictionary with processed chunks including embeddings
        """
        try:
            chunks = []
            
            # Process each page
            for page in page_data:
                if not page.get("content"):
                    continue
                    
                # Split content into chunks
                page_chunks = self._chunk_text(page["content"])
                
                for chunk_text in page_chunks:
                    chunks.append({
                        "content": chunk_text,
                        "page_number": page.get("page_number"),
                        "metadata": {
                            "source": "pdf",
                            "page_number": page.get("page_number"),
                            "file_type": "pdf"
                        }
                    })
            
            # Generate embeddings for all chunks
            if chunks:
                # Get embeddings in batches
                embeddings = []
                batch_size = 10  # Adjust based on API limits
                
                for i in range(0, len(chunks), batch_size):
                    batch = chunks[i:i + batch_size]
                    batch_texts = [chunk["content"] for chunk in batch]
                    batch_embeddings = get_text_embeddings_in_batches_sync(batch_texts)
                    
                    if batch_embeddings and len(batch_embeddings) == len(batch):
                        embeddings.extend(batch_embeddings)
                    else:
                        # If embedding generation fails, skip these chunks
                        logger.error(f"Failed to generate embeddings for batch {i//batch_size}")
                        embeddings.extend([None] * len(batch))
                
                # Add embeddings to chunks
                for i, embedding in enumerate(embeddings):
                    if i < len(chunks):
                        chunks[i]["embedding"] = embedding
            
            return {
                "chunks": chunks,
                "total_chunks": len(chunks),
                "total_pages": len(page_data)
            }
            
        except Exception as e:
            logger.error(f"Error processing PDF pages: {e}", exc_info=True)
            raise
    
    def extract_text_with_page_numbers(self, pdf_data: bytes) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF data while capturing page numbers.
        
        Args:
            pdf_data: PDF file data in bytes
            
        Returns:
            List of dictionaries with page numbers and text content
        """
        try:
            pages = []
            with fitz.open(stream=pdf_data, filetype="pdf") as doc:
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    text = page.get_text()
                    if text.strip():  # Only add non-empty pages
                        pages.append({
                            "page_number": page_num + 1,
                            "content": text
                        })
            return pages
            
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}", exc_info=True)
            raise
    
    def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the PDF file.
        
        Args:
            **kwargs: Must include 'file_data' as bytes
            
        Returns:
            Dict containing extracted page content
        """
        try:
            file_data = kwargs.get('file_data')
            if not file_data:
                raise ValueError("No file data provided")
                
            # Extract text with page numbers and format with metadata
            pages = self.extract_text_with_page_numbers(file_data)
            
            return {
                "pages": [{
                    "content": page["content"],
                    "page_number": page["page_number"],
                    "metadata": {
                        "source": "pdf",
                        "page_number": page["page_number"],
                        "file_type": "pdf"
                    }
                } for page in pages],
                "page_count": len(pages),
                "format": "pdf"
            }
            
        except Exception as e:
            logger.error(f"Error extracting content from PDF: {e}", exc_info=True)
            raise
    
    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """
        Split text into overlapping chunks.
        
        Args:
            text: Text to split into chunks
            chunk_size: Maximum size of each chunk
            overlap: Number of characters to overlap between chunks
            
        Returns:
            List of text chunks
        """
        if not text:
            return []
            
        chunks = []
        start = 0
        text_length = len(text)
        
        while start < text_length:
            end = min(start + chunk_size, text_length)
            chunks.append(text[start:end].strip())
            
            if end == text_length:
                break
                
            # Move start forward, but overlap with the previous chunk
            start = end - overlap
            
            # If we can't make progress, break to avoid infinite loop
            if start >= text_length - 1:
                break
                
        return chunks
    
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
        try:
            return generate_learning_materials_for_concept(
                store=self.store,
                file_id=file_id,
                concept=concept
            )
        except Exception as e:
            logger.error(f"Error generating learning materials for concept {concept.get('id')}: {e}", 
                        exc_info=True)
            return False
