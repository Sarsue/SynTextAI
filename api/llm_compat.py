"""
LLM Compatibility Layer

This module provides backward compatibility for LLM-related functions that were previously
implemented directly in the code. It acts as an adapter between the old function-based
interface and the new service-based architecture.
"""
import logging
from typing import List, Optional, Any, Dict

from .services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# Global cache for the embedding service instance
_embedding_service = None

def get_embedding_service():
    """Get or create the embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = embedding_service
    return _embedding_service

async def get_text_embeddings_in_batches(
    texts: List[str], 
    batch_size: int = 32,
    **kwargs
) -> List[List[float]]:
    """
    Generate embeddings for a list of texts in batches.
    
    This is a compatibility function that uses the EmbeddingService under the hood.
    
    Args:
        texts: List of text strings to generate embeddings for
        batch_size: Number of texts to process in each batch
        **kwargs: Additional arguments to pass to the embedding service
        
    Returns:
        List of embedding vectors, one for each input text
    """
    if not texts:
        return []
        
    service = get_embedding_service()
    
    # Process in batches to avoid overwhelming the API
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            batch_embeddings = await service.get_embeddings(batch, **kwargs)
            all_embeddings.extend(batch_embeddings)
        except Exception as e:
            logger.error(f"Error generating embeddings for batch {i//batch_size}: {e}")
            # For failed batches, add None placeholders to maintain list length
            all_embeddings.extend([None] * len(batch))
    
    return all_embeddings

from typing import Dict, Any, Optional, List
import base64
import io
from PIL import Image
import pytesseract
from pydantic import BaseModel, Field

# Import LLM service for text generation
from .services.llm_service import llm_service

class KeyConcept(BaseModel):
    """Represents a key concept extracted from a document."""
    title: str = Field(..., description="The title/name of the key concept")
    explanation: str = Field(..., description="Detailed explanation of the concept")
    source_page: Optional[int] = Field(None, description="Source page number (for PDFs)")
    source_timestamp_start: Optional[float] = Field(None, description="Start timestamp in seconds (for videos)")
    source_timestamp_end: Optional[float] = Field(None, description="End timestamp in seconds (for videos)")

async def generate_key_concepts_dspy(
    document_text: str,
    language: str = "english",
    comprehension_level: str = "beginner",
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Generate key concepts from document text using LLM.
    
    Args:
        document_text: The text content to extract key concepts from
        language: Language of the document
        comprehension_level: Target comprehension level (beginner, intermediate, advanced)
        **kwargs: Additional arguments for the LLM
        
    Returns:
        List of dictionaries containing key concepts with their explanations and source references
    """
    if not document_text.strip():
        return []
    
    # Prepare the prompt for the LLM
    prompt = f"""Extract the most important key concepts from the following {language} text.
    For each concept, provide a clear explanation suitable for a {comprehension_level} level.
    Format the response as a list of JSON objects with 'title' and 'explanation' fields.
    
    Text:
    {document_text[:20000]}  # Limit context size
    """
    
    try:
        # Use the LLM service to generate the concepts
        response = await llm_service.generate_text(
            prompt=prompt,
            temperature=0.3,  # Lower temperature for more focused results
            max_tokens=2000,
            **kwargs
        )
        
        # Parse the response into a list of KeyConcept objects
        try:
            # Try to parse the response as JSON
            import json
            concepts_data = json.loads(response)
            if not isinstance(concepts_data, list):
                concepts_data = [concepts_data]
                
            # Convert to KeyConcept objects and then to dicts
            return [
                KeyConcept(
                    title=concept.get('title', '').strip(),
                    explanation=concept.get('explanation', '').strip(),
                    source_page=concept.get('source_page'),
                    source_timestamp_start=concept.get('source_timestamp_start'),
                    source_timestamp_end=concept.get('source_timestamp_end'),
                ).dict(exclude_none=True)
                for concept in concepts_data
                if concept.get('title') and concept.get('explanation')
            ]
            
        except json.JSONDecodeError:
            # Fallback to simple parsing if JSON parsing fails
            logger.warning("Failed to parse LLM response as JSON, falling back to text parsing")
            return [
                {"title": "Key Concept", "explanation": response[:500]}
            ]
            
    except Exception as e:
        logger.error(f"Error generating key concepts: {e}")
        return []

async def extract_image_text(
    image_data: bytes,
    image_format: str = 'PNG',
    detail: str = 'high',
    **kwargs
) -> str:
    """
    Extract text from an image using LLM vision capabilities.
    
    Args:
        image_data: Binary image data (JPEG, PNG, etc.)
        image_format: Format of the image (PNG, JPEG, etc.)
        detail: Level of detail for image processing ('high' or 'low')
        **kwargs: Additional arguments for the LLM
        
    Returns:
        Extracted text from the image
    """
    if not image_data:
        return ""
        
    try:
        # Convert image to base64 for the LLM
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Prepare the prompt for the LLM
        prompt = """
        Extract all text from this image with high accuracy. 
        Preserve the original formatting, layout, and structure as much as possible.
        Include all visible text, including:
        - Paragraphs
        - Headings and subheadings
        - Lists and bullet points
        - Tables (as markdown tables if possible)
        - Code blocks (with proper formatting)
        
        If the image contains handwritten text, do your best to transcribe it accurately.
        If the text is in a language other than English, preserve the original language.
        
        Only return the extracted text, with no additional commentary or formatting.
        """
        
        # Prepare the message with image
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{image_format.lower()};base64,{image_base64}",
                            "detail": detail
                        }
                    }
                ]
            }
        ]
        
        # Use the LLM service to extract text
        response = await llm_service.chat(
            messages=messages,
            temperature=0.1,  # Low temperature for accurate transcription
            max_tokens=4000,  # Sufficient for most images
            **kwargs
        )
        
        return response.strip()
        
    except Exception as e:
        logger.error(f"Error extracting text from image using LLM: {e}")
        # Fallback to Tesseract if available
        try:
            logger.info("Falling back to Tesseract OCR")
            image = Image.open(io.BytesIO(image_data))
            if image.mode != 'L':
                image = image.convert('L')
            return pytesseract.image_to_string(image).strip()
        except Exception as fallback_error:
            logger.error(f"Tesseract fallback failed: {fallback_error}")
            return ""

# Test/mock functions have been removed as they were not being used
