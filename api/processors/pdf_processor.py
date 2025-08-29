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
from api.services.llm_service import llm_service
from api.services.embedding_service import embedding_service
from api.processors.processor_utils import (
    generate_learning_materials,
    LearningMaterialsSummary,
    generate_learning_materials_for_concept
)

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
            
            # Prepare content for key concept generation
            content = {
                "pages": [
                    {
                        "page_number": i + 1,
                        "content": seg.get("content", ""),
                        "metadata": {
                            "source": "pdf",
                            "page_number": i + 1,
                            "file_type": "pdf"
                        }
                    }
                    for i, seg in enumerate(processed_data.get("segments", []))
                    if isinstance(seg, dict) and seg.get("content")
                ]
            }
            
            try:
                # Generate key concepts using dspy
                key_concepts = await self.generate_key_concepts(content)
                
                # Store key concepts
                concepts_processed = 0
                for concept in key_concepts:
                    if not isinstance(concept, dict):
                        continue
                        
                    # Format the concept for the repository
                    key_concept_data = {
                        "concept_title": concept.get("concept_title", ""),
                        "concept_explanation": concept.get("concept_explanation", ""),
                        "source_page_number": concept.get("source_page_number"),
                        "is_custom": False
                    }
                    
                    # Store the concept
                    result = await self.store.learning_material_repo.add_key_concept(
                        file_id=file_id,
                        key_concept_data=key_concept_data
                    )
                    
                    if result and result.get("id"):
                        concepts_processed += 1
                    else:
                        logger.warning(f"Failed to save concept: {concept.get('concept_title')}")
                
                # Store segments and chunks in the database
                success = await self.store.file_repo.update_file_with_chunks(
                    user_id=user_id,
                    filename=filename,
                    file_type="pdf",
                    extracted_data=processed_data.get("segments", [])
                )
                
                if not success:
                    logger.error(f"Failed to store PDF segments for file_id: {file_id}")
                    return {
                        "success": False,
                        "error": "Failed to store processed segments",
                        "file_id": file_id,
                        "metadata": {
                            "processor_type": "pdf",
                            "page_count": len(page_data),
                            "segment_count": len(processed_data.get("segments", [])),
                            "key_concepts_count": concepts_processed
                        }
                    }
                
                # Update file status to completed
                await self.store.file_repo.update_file_status(
                    file_id=file_id,
                    status="completed"
                )
                
                logger.info(f"Successfully processed PDF with {concepts_processed} key concepts")
                
                return {
                    "success": True,
                    "file_id": file_id,
                    "metadata": {
                        "processor_type": "pdf",
                        "page_count": len(page_data),
                        "segment_count": len(processed_data.get("segments", [])),
                        "key_concepts_count": concepts_processed
                    }
                }
                    
            except Exception as e:
                logger.error(f"Error generating key concepts: {e}", exc_info=True)
                return {
                    "success": True,  # Still consider it a success since we have the content
                    "file_id": file_id,
                    "warning": f"Key concept generation failed: {str(e)}",
                    "metadata": {
                        "processor_type": "pdf",
                        "page_count": len(page_data),
                        "segment_count": len(processed_data.get("segments", [])),
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
            Dictionary with processed segments and chunks including embeddings
        """
        try:
            segments = []
            
            # Process each page as a segment
            for page in page_data:
                if not page.get("content"):
                    continue
                    
                # Split content into chunks
                page_chunks = self._chunk_text(page["content"])
                chunks_data = []
                
                # Generate embeddings for each chunk
                if page_chunks:
                    # Get embeddings in batches
                    embeddings = []
                    batch_size = 10  # Adjust based on API limits
                    
                    for i in range(0, len(page_chunks), batch_size):
                        batch = page_chunks[i:i + batch_size]
                        batch_embeddings = get_text_embeddings_in_batches_sync(batch)
                        
                        if batch_embeddings and len(batch_embeddings) == len(batch):
                            embeddings.extend(batch_embeddings)
                        else:
                            # If embedding generation fails, skip these chunks
                            logger.error(f"Failed to generate embeddings for batch {i//batch_size}")
                            embeddings.extend([None] * len(batch))
                    
                    # Prepare chunks data with embeddings
                    for embedding in embeddings:
                        if embedding is not None:
                            chunks_data.append({"embedding": embedding})
                
                # Add segment with its chunks
                segments.append({
                    "content": page["content"],
                    "page_number": page.get("page_number"),
                    "metadata": {
                        "source": "pdf",
                        "page_number": page.get("page_number")
                    },
                    "chunks": chunks_data
                })
            
            return {
                "segments": segments,
                "total_segments": len(segments),
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
    
    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the PDF content.
        
        Args:
            content: Dictionary containing 'pages' with page content
            **kwargs: Additional parameters
            
        Returns:
            List of key concept dictionaries
        """
        try:
            if not content or not isinstance(content, dict) or 'pages' not in content:
                logger.warning("Invalid content: expected dict with 'pages' key")
                return []

            # Extract text from all pages
            full_text = "\n\n".join([
                f"Page {page.get('page_number', 0)}:\n{page.get('content', '').strip()}"
                for page in content.get('pages', [])
                if page.get('content', '').strip()
            ])
            
            if not full_text.strip():
                logger.warning("No text content found in PDF")
                return []

            # Generate key concepts using the async DSPy function
            key_concepts = await generate_key_concepts_dspy(document_text=full_text)
            
            # Format the key concepts to match repository expectations
            formatted_concepts = []
            for concept in key_concepts:
                if not isinstance(concept, dict):
                    continue
                    
                formatted_concept = {
                    "concept_title": concept.get("concept_title", ""),
                    "concept_explanation": concept.get("concept_explanation", ""),
                    "source_page_number": concept.get("source_page_number"),
                    "is_custom": False
                }
                
                # Include any additional fields that might be in the concept
                for field in ["source_text", "relevance_score", "related_concepts"]:
                    if field in concept:
                        formatted_concept[field] = concept[field]
                
                formatted_concepts.append(formatted_concept)
            
            return formatted_concepts
            
        except Exception as e:
            logger.error(f"Error in generate_key_concepts: {e}", exc_info=True)
            return []

    # Learning materials generation is now handled by the base class
