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


def get_text_embeddings_in_batches_sync(
    texts: List[str], 
    batch_size: int = 32,
    **kwargs
) -> List[List[float]]:
    """
    Synchronous version of get_text_embeddings_in_batches.
    
    This function should only be used when async is not an option, such as in Celery tasks.
    It runs the async method in a new event loop.
    
    Args:
        texts: List of text strings to generate embeddings for
        batch_size: Number of texts to process in each batch
        **kwargs: Additional arguments to pass to the embedding service
        
    Returns:
        List of embedding vectors, one for each input text
    """
    import asyncio
    
    try:
        # Try to get the running event loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # If there's no running loop, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    # Run the async method in the event loop
    return loop.run_until_complete(
        get_text_embeddings_in_batches(texts, batch_size, **kwargs)
    )

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

def generate_key_concepts_dspy_sync(
    document_text: str,
    language: str = "english",
    comprehension_level: str = "beginner",
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Synchronous version of generate_key_concepts_dspy.
    
    This function should only be used when async is not an option, such as in Celery tasks.
    It runs the async method in a new event loop.
    
    Args:
        document_text: The text content to extract key concepts from
        language: Language of the document
        comprehension_level: Target comprehension level (beginner, intermediate, advanced)
        **kwargs: Additional arguments for the LLM
        
    Returns:
        List of dictionaries containing key concepts with their explanations and source references
    """
    import asyncio
    
    try:
        # Try to get the running event loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # If there's no running loop, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    # Run the async method in the event loop
    return loop.run_until_complete(
        generate_key_concepts_dspy(document_text, language, comprehension_level, **kwargs)
    )


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
    3. Identify 1-3 related concepts (if any) and describe their relationships
    4. Keep explanations between 1-3 sentences
    
    FORMAT: Return a valid JSON array of objects, where each object has:
    {{
        "title": "Concept Title",
        "explanation": "Detailed explanation here...",
        "related_concepts": [
            {{
                "title": "Related Concept Title",
                "relationship": "how this concept relates to the main concept"
            }}
        ]
    }}
    
    EXAMPLE RESPONSE:
    [
        {{
            "title": "Machine Learning",
            "explanation": "A field of AI that enables systems to learn from data without explicit programming.",
            "related_concepts": [
                {{
                    "title": "Neural Networks",
                    "relationship": "are a type of machine learning model inspired by the human brain"
                }},
                {{
                    "title": "Supervised Learning",
                    "relationship": "is a category of machine learning where models are trained on labeled data"
                }}
            ]
        }},
        {{
            "title": "Neural Networks",
            "explanation": "Computational models inspired by the human brain, consisting of interconnected nodes that process information.",
            "related_concepts": [
                {{
                    "title": "Deep Learning",
                    "relationship": "uses neural networks with many layers to model complex patterns"
                }}
            ]
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
        import json
        import re
        from typing import List, Dict, Any, Optional, Callable, TypeVar, Type, Union
        from pydantic import ValidationError
        
        T = TypeVar('T')
        
        def try_parse_json(
            text: str, 
            attempts: List[Callable[[str], Any]],
            error_context: Optional[Dict[str, Any]] = None
        ) -> Any:
            """Attempt to parse JSON with multiple strategies.
            
            Args:
                text: The text to parse as JSON
                attempts: List of parsing functions to try
                error_context: Additional context for error logging
                
            Returns:
                The parsed JSON data
                
            Raises:
                ValueError: If all parsing attempts fail
            """
            last_error = None
            
            for i, attempt in enumerate(attempts, 1):
                try:
                    result = attempt(text)
                    logger.debug(f"Successfully parsed JSON using attempt {i}/{len(attempts)}")
                    return result
                except (json.JSONDecodeError, IndexError, AttributeError, ValueError) as e:
                    last_error = e
                    logger.debug(
                        f"JSON parse attempt {i}/{len(attempts)} failed: {e}" +
                        (f" | Context: {error_context}" if error_context else "")
                    )
            
            logger.warning(
                "All JSON parse attempts failed",
                extra={
                    'response': text[:500] + ('...' if len(text) > 500 else ''),
                    'error': str(last_error) if last_error else 'Unknown error'
                }
            )
            raise last_error or ValueError("No parse attempts were made")
        
        def clean_json_response(response: str) -> str:
            """Clean and normalize the LLM response before JSON parsing."""
            # Remove markdown code blocks if present
            cleaned = response.strip()
            json_match = re.search(r'```(?:json\r?\n)?(.*?)\r?\n```', cleaned, re.DOTALL)
            if json_match:
                cleaned = json_match.group(1).strip()
            
            # If we don't have valid JSON delimiters, try to extract them
            if not (cleaned.startswith('[') and cleaned.endswith(']')) and \
               not (cleaned.startswith('{') and cleaned.endswith('}')):
                # Look for JSON array or object in the response
                json_array_match = re.search(r'(\[\s*\{.*?\}\s*\])', cleaned, re.DOTALL)
                if json_array_match:
                    cleaned = json_array_match.group(1).strip()
                else:
                    json_object_match = re.search(r'(\{.*?\})', cleaned, re.DOTALL)
                    if json_object_match:
                        cleaned = f"[{json_object_match.group(1).strip()}]"
            
            return cleaned
        
        # Define JSON parsing strategies from most to least strict
        json_parse_strategies = [
            # 1. Direct parse (strict)
            json.loads,
            
            # 2. Fix trailing commas
            lambda x: json.loads(re.sub(r',(\s*[}\]])', r'\1', x)),
            
            # 3. Extract JSON object from text
            lambda x: json.loads('{' + x.split('{', 1)[1].rsplit('}', 1)[0] + '}'),
            
            # 4. Fix common formatting issues
            lambda x: json.loads(
                re.sub(r'([^\\])"', r'\1\\"',  # Fix unescaped quotes
                re.sub(r'([{\[,])\s*([}\\],])', r'\1\2',  # Remove spaces between brackets
                re.sub(r'([:,\[{])\s*\n\s*', r'\1',  # Remove newlines after delimiters
                re.sub(r'"([^"]*?)"\s*:', r'"\1":',  # Fix missing quotes around keys
                re.sub(r':\s*\n\s*"', ': "',  # Fix newlines after colons
                x.strip())))))),
            
            # 5. Aggressive fixes (last resort)
            lambda x: json.loads(
                re.sub(r'```(?:json\r?\n)?|```', '',  # Remove markdown
                re.sub(r'([^\\])"', r'\\\1"',  # Fix unescaped quotes
                re.sub(r'([\{\s,])(\w+)(\s*:)\s*', r'\1"\2"\3',  # Fix unquoted keys
                re.sub(r',(\s*[}\]])', r'\1',  # Fix trailing commas
                re.sub(r'}\s*{', '},{',  # Fix missing commas
                x.replace("'", '"')  # Fix single quotes
                ))))).strip())
        ]
        
        # Clean and parse the response
        cleaned_response = clean_json_response(response)
        
        try:
            concepts_data = try_parse_json(
                cleaned_response,
                json_parse_strategies,
                error_context={"response_length": len(response)}
            )
        except Exception as e:
            logger.warning("Failed to parse LLM response as JSON, falling back to text extraction")
            raise ValueError(f"Failed to parse LLM response: {str(e)}") from e
        
        # Normalize and validate the parsed concepts
        def normalize_concepts(data: Any) -> List[Dict[str, Any]]:
            """Normalize and validate parsed concepts into a consistent format."""
            if not isinstance(data, (list, dict, str)):
                logger.warning(f"Unexpected concept data type: {type(data).__name__}")
                return []
            
            # Convert single concept to list
            if isinstance(data, dict):
                data = [data]
            # Convert list of strings to list of concept objects
            elif isinstance(data, str) or all(isinstance(x, str) for x in data):
                data = [{'title': f"Concept {i+1}", 'explanation': x.strip()} 
                       for i, x in enumerate([data] if isinstance(data, str) else data)]
            
            # Validate and normalize each concept
            valid_concepts = []
            for i, concept in enumerate(data, 1):
                try:
                    if not isinstance(concept, (dict, str)):
                        logger.debug(f"Skipping invalid concept at index {i}: not a dict or string")
                        continue
                    
                    # Handle string concepts
                    if isinstance(concept, str):
                        concept = {'title': f"Concept {i}", 'explanation': concept.strip()}
                    
                    # Get related concepts if available
                    related_concepts = concept.get('related_concepts', [])
                    relationship_text = ''
                    
                    if related_concepts and isinstance(related_concepts, list):
                        relationship_text = '\n\nRelated Concepts:\n' + '\n'.join(
                            f'- {rc.get("title", "")}: {rc.get("relationship", "")}'
                            for rc in related_concepts
                            if rc and isinstance(rc, dict) and rc.get('title') and rc.get('relationship')
                        )
                    
                    # Normalize field names and include relationships in explanation
                    explanation = concept.get('concept_explanation') or concept.get('explanation', '').strip()
                    normalized = {
                        'title': concept.get('concept_title') or concept.get('title', '').strip(),
                        'explanation': f"{explanation}{relationship_text}".strip(),
                        'source_page': concept.get('source_page'),
                        'source_timestamp_start': concept.get('source_timestamp_start'),
                        'source_timestamp_end': concept.get('source_timestamp_end')
                    }
                    
                    # Skip empty concepts
                    if not normalized['title'] or not normalized['explanation']:
                        logger.debug(f"Skipping concept with empty title or explanation at index {i}")
                        continue
                    
                    # Validate using Pydantic model
                    key_concept = KeyConcept.parse_obj(normalized)
                    
                    # Convert to dict with expected field names
                    concept_dict = key_concept.dict(exclude_none=True)
                    concept_dict['concept_title'] = concept_dict.pop('title')
                    concept_dict['concept_explanation'] = concept_dict.pop('explanation')
                    
                    valid_concepts.append(concept_dict)
                    
                except ValidationError as ve:
                    logger.debug(f"Skipping invalid concept at index {i}: {str(ve)}")
                except Exception as e:
                    logger.debug(f"Error processing concept at index {i}: {str(e)}")
            
            logger.info(f"Extracted {len(valid_concepts)} valid concepts from LLM response")
            return valid_concepts
        
        # Process and validate the parsed concepts
        key_concepts = normalize_concepts(concepts_data)
        
        logger.info(f"Successfully extracted {len(key_concepts)} valid concepts")
        return key_concepts
            
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
