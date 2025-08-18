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
        
    Raises:
        ValueError: If LLM service is not properly initialized or if concept extraction fails
    """
    if not document_text or not document_text.strip():
        logger.warning("Empty or None document text provided for concept extraction")
        return []
    
    logger.info(f"Starting key concept extraction for {len(document_text)} characters of text")
    
    # Log LLM service status
    from .services.llm_service import llm_service
    logger.info(f"LLM Service Status - Active Provider: {llm_service.active_provider or 'None'}")
    
    if not llm_service.active_provider:
        error_msg = "No active LLM provider available. Please set MISTRAL_API_KEY or GOOGLE_API_KEY environment variable."
        logger.error(error_msg)
        return []
    
    # Prepare a more structured prompt with clear examples
    prompt = f"""
    You are an expert at extracting and explaining key concepts from educational content.
    
    TASK: Extract 5-10 key concepts from the following {language} text. For each concept:
    1. Provide a clear, concise title (3-7 words)
    2. Write a detailed explanation suitable for a {comprehension_level} level
    3. Keep explanations between 1-3 sentences
    
    FORMAT: Return a valid JSON array of objects, where each object has:
    {{
        "title": "Concept Title",
        "explanation": "Detailed explanation here..."
    }}
    
    EXAMPLE RESPONSE:
    [
        {{
            "title": "Machine Learning",
            "explanation": "A field of AI that enables systems to learn from data without explicit programming."
        }},
        {{
            "title": "Neural Networks",
            "explanation": "Computational models inspired by the human brain, consisting of interconnected nodes that process information."
        }}
    ]
    
    CONTENT TO ANALYZE:
    {document_text[:15000]}  # Limit context to avoid token limits
    
    IMPORTANT: Return ONLY valid JSON. Do not include any other text or markdown formatting.
    """
    
    try:
        logger.info("Sending request to LLM for concept extraction...")
        
        # Use the LLM service to generate the concepts
        response = await llm_service.generate_text(
            prompt=prompt,
            temperature=0.3,  # Lower temperature for more focused results
            max_tokens=2000,
            **kwargs
        )
        
        if not response:
            logger.error("Received empty response from LLM service")
            return []
            
        logger.debug(f"Raw LLM response for key concepts: {response[:500]}...")
        
        # Clean and parse the response
        try:
            import json
            import re
            
            # Clean up the response - handle markdown code blocks and other artifacts
            cleaned_response = response.strip()
            
            # Extract JSON from markdown code blocks if present
            json_match = re.search(r'```(?:json\n)?(.*?)\n```', cleaned_response, re.DOTALL)
            if json_match:
                cleaned_response = json_match.group(1).strip()
            
            # Handle common JSON formatting issues
            cleaned_response = re.sub(r',\s*]', ']', cleaned_response)  # Trailing commas
            cleaned_response = re.sub(r',\s*\}', '}', cleaned_response)  # Trailing commas in objects
            
            # Try to parse the cleaned response as JSON
            concepts_data = json.loads(cleaned_response)
            
            # Ensure we have a list
            if not isinstance(concepts_data, list):
                if isinstance(concepts_data, dict):
                    concepts_data = [concepts_data]
                else:
                    logger.error(f"Expected list of concepts, got {type(concepts_data).__name__}")
                    return []
            
            logger.info(f"Successfully parsed {len(concepts_data)} concepts from LLM response")
            
            # Convert to KeyConcept objects and validate
            valid_concepts = []
            for i, concept in enumerate(concepts_data, 1):
                try:
                    if not isinstance(concept, dict):
                        logger.warning(f"Skipping concept {i} - expected dict, got {type(concept).__name__}")
                        continue
                        
                    title = concept.get('title', '')
                    if title and isinstance(title, str):
                        title = title.strip()
                    
                    explanation = concept.get('explanation', '')
                    if explanation and isinstance(explanation, str):
                        explanation = explanation.strip()
                    
                    if not title or not explanation:
                        logger.warning(f"Skipping concept {i} - missing title or explanation")
                        logger.debug(f"Title: {title}\nExplanation: {explanation}")
                        continue
                        
                    # Create and validate the key concept
                    key_concept = KeyConcept(
                        title=title[:500],  # Limit length to prevent DB issues
                        explanation=explanation[:2000],
                        source_page=concept.get('source_page'),
                        source_timestamp_start=concept.get('source_timestamp_start'),
                        source_timestamp_end=concept.get('source_timestamp_end'),
                    )
                    
                    valid_concepts.append(key_concept.dict(exclude_none=True))
                    
                except Exception as e:
                    logger.error(f"Error processing concept {i}: {str(e)}", exc_info=True)
            
            logger.info(f"Successfully extracted {len(valid_concepts)} valid concepts")
            return valid_concepts
            
        except json.JSONDecodeError as je:
            logger.error(f"JSON decode error: {str(je)}")
            logger.warning("Failed to parse LLM response as JSON, attempting text extraction...")
            
            # Try to extract concepts from plain text response
            try:
                concepts = []
                # Look for patterns like "1. Title: ... Explanation: ..."
                pattern = r'(?:\d+\.\s*)?([^:\n]+?)\s*:\s*([^\n]+)'
                matches = re.finditer(pattern, response, re.IGNORECASE | re.MULTILINE)
                
                for match in matches:
                    title = match.group(1).strip()
                    explanation = match.group(2).strip()
                    
                    if title and explanation and len(title) < 100 and len(explanation) < 500:
                        concepts.append({
                            'title': title,
                            'explanation': explanation
                        })
                
                if concepts:
                    logger.info(f"Extracted {len(concepts)} concepts from text response")
                    return concepts
                
                logger.warning("No concepts could be extracted from text response")
                return []
                
            except Exception as e:
                logger.error(f"Error during text extraction fallback: {str(e)}")
                return []
                
    except Exception as e:
        logger.error(f"Error in generate_key_concepts_dspy: {str(e)}", exc_info=True)
        return []
    
    concepts = []
    try:
        import re
        concept_matches = re.findall(r'(?i)concept\s*:?\s*(.+?)(?=\n\s*explanation|\n\s*\n|$)', response, re.DOTALL)
        explanation_matches = re.findall(r'(?i)explanation\s*:?\s*(.+?)(?=\n\s*concept|\n\s*\n|$)', response, re.DOTALL)
        
        concepts = [
            {'title': title.strip(), 'explanation': explanation.strip()}
            for title, explanation in zip(concept_matches, explanation_matches)
        ]
            
        if concepts:
            logger.info(f"Extracted {len(concepts)} concepts")
            return concepts
            
        return [{"title": "Key Concept", "explanation": response[:500]}]
            
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
