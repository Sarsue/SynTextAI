"""
Text processor for handling text files and images.
Extracts text content, generates chunks with embeddings, and identifies key concepts.
"""
import logging
import base64
from typing import Dict, List, Any, Optional

from processors.base_processor import FileProcessor
from llm_service import extract_image_text, get_text_embeddings_in_batches, generate_key_concepts_dspy
from utils import chunk_text

logger = logging.getLogger(__name__)

class TextProcessor(FileProcessor):
    """Processor for text files and images."""
    
    def __init__(self, store):
        """
        Initialize the TextProcessor with a document store.
        
        Args:
            store: Document store for saving processed data
        """
        self.store = store
    
    async def process(self, 
                     file_data: bytes,
                     user_id: int, 
                     file_id: int, 
                     filename: str, 
                     **kwargs) -> Dict[str, Any]:
        """
        Process a text file or image and store the results.
        
        Args:
            file_data: Raw file data as bytes
            user_id: User ID
            file_id: File ID
            filename: Name of the file
            **kwargs: Additional parameters
            
        Returns:
            Dict containing processing results
        """
        try:
            # Determine file type from extension
            file_extension = filename.split('.')[-1].lower() if '.' in filename else ''
            
            # Extract content based on file type
            if file_extension in ['txt']:
                content = self.extract_text_content(file_data)
                file_type = 'text'
            elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
                content = await self.extract_image_content(file_data)
                file_type = 'image'
            else:
                return {
                    'success': False,
                    'error': f'Unsupported file type: {file_extension}'
                }
                
            # No content extracted
            if not content:
                return {
                    'success': False,
                    'error': 'No content could be extracted'
                }
                
            # Generate chunked content with embeddings
            processed_data = await self.generate_embeddings(content)
            if not processed_data:
                return {
                    'success': False,
                    'error': 'Failed to generate embeddings'
                }
                
            # Store the chunks
            chunk_count = 0
            for item in processed_data:
                chunks = item.get('chunks', [])
                for chunk in chunks:
                    self.store.add_chunk(
                        user_id=user_id,
                        file_id=file_id,
                        page_num=item.get('page_number', 0),
                        chunk_text=chunk.get('text', ''),
                        embedding=chunk.get('embedding', [])
                    )
                    chunk_count += 1
                    
            # Generate and store key concepts
            all_text = ' '.join([item.get('content', '') for item in content if item.get('content', '').strip()])
            key_concepts = await self.generate_key_concepts(all_text)
            key_concept_count = 0
            if key_concepts:
                for i, concept in enumerate(key_concepts):
                    self.store.add_key_concept(
                        file_id=file_id,
                        concept_title=concept.get("concept_title"),
                        concept_explanation=concept.get("concept_explanation"),
                        display_order=i + 1,
                        source_page_number=concept.get("source_page_number")
                    )
                    key_concept_count += 1
            
            # Update file processing status
            self.store.update_file_processing_status(file_id, True)
            
            return {
                'success': True,
                'file_type': file_type,
                'chunk_count': chunk_count,
                'key_concept_count': key_concept_count
            }
                
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            # Mark as processed with error
            if self.store:
                self.store.update_file_processing_status(
                    file_id, True, status="error", 
                    error_message=f"Processing error: {str(e)[:100]}"
                )
            return {
                'success': False,
                'error': str(e)
            }
    
    def extract_text_content(self, file_data: bytes) -> List[Dict[str, Any]]:
        """
        Extract content from a text file.
        
        Args:
            file_data: Raw text file data
            
        Returns:
            List of dicts with page_number, content, and chunks
        """
        try:
            text = file_data.decode('utf-8')
            chunks = chunk_text(text)
            
            return [{
                'page_number': 0,
                'content': text,
                'chunks': chunks
            }]
        except Exception as e:
            logger.error(f"Error extracting text content: {e}", exc_info=True)
            return []
    
    async def extract_image_content(self, image_data: bytes) -> List[Dict[str, Any]]:
        """
        Extract text from an image using OCR.
        
        Args:
            image_data: Raw image data
            
        Returns:
            List of dicts with page_number, content, and chunks
        """
        try:
            encoded_data = base64.b64encode(image_data).decode('utf-8')
            image_text = extract_image_text(encoded_data)
            chunks = chunk_text(image_text)
            
            return [{
                'page_number': 0,
                'content': image_text,
                'chunks': chunks
            }]
        except Exception as e:
            logger.error(f"Error extracting image content: {e}", exc_info=True)
            return []
    
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract content from file based on type.
        Implementation of abstract method.
        """
        file_data = kwargs.get('file_data')
        file_type = kwargs.get('file_type')
        
        if file_type == 'text':
            return self.extract_text_content(file_data)
        elif file_type in ['image', 'jpg', 'jpeg', 'png', 'gif']:
            return await self.extract_image_content(file_data)
        else:
            return []
    
    async def generate_embeddings(self, content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Generate embeddings for chunked content.
        
        Args:
            content: List of content items with chunks
            
        Returns:
            List of content items with embeddings added to chunks
        """
        try:
            processed_data = []
            
            for item in content:
                chunks = item.get('chunks', [])
                chunk_texts = [c for c in chunks]
                
                if chunk_texts:
                    # Get embeddings for all chunks
                    embeddings = await get_text_embeddings_in_batches([c for c in chunk_texts])
                    
                    # Create chunks with embeddings
                    chunks_with_embeddings = []
                    for i, chunk in enumerate(chunk_texts):
                        if i < len(embeddings):
                            chunks_with_embeddings.append({
                                'text': chunk,
                                'embedding': embeddings[i]
                            })
                    
                    # Create processed item
                    processed_item = {
                        'page_number': item.get('page_number', 0),
                        'content': item.get('content', ''),
                        'chunks': chunks_with_embeddings
                    }
                    processed_data.append(processed_item)
            
            return processed_data
        
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            return []
    
    async def generate_key_concepts(self, document_text: str, **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from document text.
        
        Args:
            document_text: Full document text
            
        Returns:
            List of key concepts
        """
        try:
            if not document_text or not document_text.strip():
                logger.warning("Empty document text, skipping key concept generation")
                return []
                
            key_concepts = generate_key_concepts_dspy(document_text=document_text)
            return key_concepts
        
        except Exception as e:
            logger.error(f"Error generating key concepts: {e}", exc_info=True)
            return []
    
    async def generate_learning_materials(self, 
                                        file_id: str,
                                        key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate learning materials based on key concepts.
        Currently not implemented for text files.
        
        Returns:
            Empty dict as this is not implemented for text files
        """
        # Not implemented for text files
        return {}
