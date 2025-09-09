"""
Compatibility layer for embeddings, LLM-based key concept extraction, and OCR text extraction.
Provides utility functions with better error handling, event loop safety, and JSON parsing resilience.
"""

import asyncio
import json
import logging
import re
from typing import List, Dict, Any

from PIL import Image
import pytesseract
import io

# Import embedding + LLM services
from .services.embedding_service import embedding_service
from .services.llm_service import llm_service
from .utils.deterministic import (
    stable_hash, 
    deterministic_cache,
    validate_and_clean_text,
    safe_json_parse
)

logger = logging.getLogger(__name__)

# Cache service instances
_embedding_service = None
_llm_service = None

# Constants for deterministic behavior
MAX_CONCEPT_LENGTH = 8000   # Characters
MIN_CONCEPT_LENGTH = 100    # Characters
MAX_CONCEPTS = 5
DEFAULT_CONCEPT_TEMPERATURE = 0.3
DEFAULT_CONCEPT_MAX_TOKENS = 1500


# -------------------------------
# Embedding utilities
# -------------------------------

def get_embedding_service():
    """Return a singleton embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = embedding_service
    return _embedding_service


async def get_text_embeddings_in_batches(
    texts: List[str],
    batch_size: int = 16,
    **kwargs
) -> List[List[float]]:
    """Generate embeddings for a list of texts in batches."""
    service = get_embedding_service()
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            logger.debug(f"Embedding batch {i//batch_size+1} of size {len(batch)}")
            batch_embeddings = await service.get_embeddings(batch, **kwargs)
            all_embeddings.extend(batch_embeddings)
        except Exception as e:
            logger.error(f"Embedding batch {i//batch_size+1} failed: {e}", exc_info=True)
            # Skip failed batch rather than inserting None
            all_embeddings.extend([] for _ in batch)

    return all_embeddings


# -------------------------------
# Event loop helper
# -------------------------------

def run_async(coro):
    """Safely run an async coroutine in sync contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        return loop.run_until_complete(coro)


# -------------------------------
# Key concept extraction
# -------------------------------

@deterministic_cache(max_size=1000)
async def generate_key_concepts_dspy(document_text: str) -> List[Dict[str, str]]:
    """Extract key concepts from text using LLM in a deterministic way."""
    cleaned_text = validate_and_clean_text(
        document_text, 
        min_length=MIN_CONCEPT_LENGTH,
        max_length=MAX_CONCEPT_LENGTH
    )
    if not cleaned_text:
        logger.warning("Invalid or empty document text provided")
        return []
    
    # Prompts
    system_prompt = """You are an expert at analyzing educational content and extracting key concepts.
    Identify the 3-5 most important concepts and explain them clearly.
    Respond ONLY with valid JSON:
    [
      {"concept_title": "...", "concept_explanation": "..."},
      ...
    ]"""
    
    user_prompt = f"""Extract 3-5 key concepts from the following educational content:
    
    {cleaned_text}
    
    Return the concepts as a JSON array."""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        global _llm_service
        if _llm_service is None:
            _llm_service = llm_service
        
        response = await _llm_service.chat(
            messages=messages,
            temperature=DEFAULT_CONCEPT_TEMPERATURE,
            max_tokens=DEFAULT_CONCEPT_MAX_TOKENS,
            top_p=0.95
        )
        
        if not response or not isinstance(response, str):
            logger.error("Empty or invalid response from LLM service")
            return []
        
        # Parse & validate
        concepts, error = safe_json_parse(response.strip(), list)
        if error or not concepts:
            logger.error(f"Failed to parse LLM concepts: {error}")
            return []
        
        valid_concepts = []
        for concept in concepts[:MAX_CONCEPTS]:
            if isinstance(concept, dict):
                title = concept.get('concept_title', '').strip()
                explanation = concept.get('concept_explanation', '').strip()
                if title and explanation:
                    valid_concepts.append({
                        'concept_title': title[:200],
                        'concept_explanation': explanation[:1000]
                    })
        
        logger.info(f"Extracted {len(valid_concepts)} valid concepts")
        return valid_concepts
    
    except Exception as e:
        logger.error(f"Error in generate_key_concepts_dspy: {str(e)}", exc_info=True)
        return []


def generate_key_concepts_dspy_sync(document_text: str) -> List[Dict[str, str]]:
    """Sync wrapper for key concept extraction."""
    return run_async(generate_key_concepts_dspy(document_text))


# -------------------------------
# OCR (image text extraction)
# -------------------------------

def extract_image_text(image_data: bytes) -> str:
    """Extract text from an image using OCR (Tesseract)."""
    try:
        image = Image.open(io.BytesIO(image_data)).convert("L")
        text = pytesseract.image_to_string(image, lang="eng")
        return text.strip()
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}", exc_info=True)
        return ""
