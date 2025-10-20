import os
import logging
import random
import time
from typing import List, Optional
import json
import re
import numpy as np
from dotenv import load_dotenv
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from requests.exceptions import Timeout, RequestException
import requests
import tiktoken
from sentence_transformers import SentenceTransformer
import google.generativeai as genai
import dspy
from pydantic import BaseModel, ValidationError, RootModel

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Pydantic models
class KeyConcept(BaseModel):
    concept_title: str
    concept_explanation: str
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None

class KeyConceptsResponse(RootModel[List[KeyConcept]]):
    root: List[KeyConcept]

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# Validate environment variables
if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY not found in environment variables.")
    raise ValueError("GOOGLE_API_KEY is required")
if not MISTRAL_API_KEY:
    logger.warning("MISTRAL_API_KEY not found, Mistral client not initialized.")
logger.info(f"Environment check - GOOGLE_API_KEY present: {bool(GOOGLE_API_KEY)}")
logger.info(f"Environment check - MISTRAL_API_KEY present: {bool(MISTRAL_API_KEY)}")

# Initialize Google GenAI
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    logger.info("Google GenAI configured successfully.")
    models = genai.list_models()
    logger.info(f"Available Google models: {[model.name for model in models]}")
except Exception as e:
    logger.error(f"Failed to configure Google GenAI: {e}")
    raise

# Initialize Mistral client
mistral_client = MistralClient(api_key=MISTRAL_API_KEY) if MISTRAL_API_KEY else None
if mistral_client:
    logger.info("Mistral client initialized.")
else:
    logger.warning("Mistral client not initialized.")

# Model configuration
GEMINI_MODEL_NAME = "gemini-2.5-pro"
MAX_TOKENS_CONTEXT = 32768
BASE_DELAY = 1

# DSPy configuration with Google GenAI
try:
    gemini_lm = dspy.LM(
        model=GEMINI_MODEL_NAME,
        api_key=GOOGLE_API_KEY,
        max_tokens=2048,
        temperature=0.1,
        custom_llm_provider="google_genai"  # Explicitly use google.generativeai
    )
    dspy.settings.configure(lm=gemini_lm)
    logger.info(f"DSPy configured with Google GenAI model: {GEMINI_MODEL_NAME}, provider: google_genai")
    # Test Google GenAI directly
    model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    test_response = model.generate_content("Test")
    logger.info(f"Google GenAI test response: {test_response.text}")
except Exception as e:
    logger.error(f"Failed to configure DSPy with Google GenAI: {e}")
    raise

# DSPy Signatures
class GenerateExplanation(dspy.Signature):
    """Generates an explanation for the given context."""
    context = dspy.InputField()
    language = dspy.InputField(default="English")
    comprehension_level = dspy.InputField(default="Beginner")
    explanation = dspy.OutputField()

class ExtractKeyConcepts(dspy.Signature):
    """Extract key concepts from a document or transcript."""
    document_content = dspy.InputField()
    document_type = dspy.InputField(default="document content")
    content_instruction = dspy.InputField()
    key_concepts_json = dspy.OutputField()

class SummarizeSignature(dspy.Signature):
    """Generates a concise summary of the provided text."""
    document_text = dspy.InputField()
    language = dspy.InputField(default="English")
    comprehension_level = dspy.InputField(default="Beginner")
    summary = dspy.OutputField()

# DSPy Predictors
explain_predictor = dspy.Predict(GenerateExplanation)
key_concept_extractor = dspy.Predict(ExtractKeyConcepts)
summary_predictor = dspy.Predict(SummarizeSignature)

def parse_llm_json(raw_json: str) -> List[dict]:
    """Parse JSON from LLM output."""
    try:
        cleaned_json = re.sub(r"```(?:json)?\n?|\n?```", "", raw_json).strip()
        return json.loads(cleaned_json)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {e}. Raw output: {raw_json[:200]}...")
        return []

def generate_key_concepts_direct_google(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[dict]:
    """Extract key concepts using direct Google GenAI API."""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        prompt = f"""Analyze this {language} {'video transcript' if is_video else 'document'} and extract key concepts for a {comprehension_level} audience:
        {document_text[:8000]}
        Return a JSON array of concepts with fields: concept_title (string), concept_explanation (string), source_page_number (integer or null), source_video_timestamp_start_seconds (integer or null), source_video_timestamp_end_seconds (integer or null)."""
        
        response = model.generate_content(prompt)
        concepts = parse_llm_json(response.text)
        
        valid_concepts = []
        for concept in concepts:
            try:
                standardized = {
                    "concept_title": concept.get("concept_title", "Untitled Concept"),
                    "concept_explanation": concept.get("concept_explanation", ""),
                    "source_page_number": concept.get("source_page_number", None),
                    "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds", None),
                    "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds", None)
                }
                KeyConcept(**standardized)
                valid_concepts.append(standardized)
            except ValidationError as ve:
                logger.warning(f"Invalid concept format: {ve}. Concept: {concept}")
                continue
        
        logger.info(f"Direct Google GenAI extracted {len(valid_concepts)} concepts")
        return valid_concepts
    except Exception as e:
        logger.error(f"Error with direct Google GenAI: {e}")
        return []

def generate_key_concepts_mistral_fallback(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[dict]:
    """Fallback to Mistral."""
    if not mistral_client:
        logger.error("Mistral client not initialized.")
        return []
    
    try:
        prompt = (
            f"Analyze this {language} {'video transcript' if is_video else 'document'} and extract key concepts for a {comprehension_level} audience:\n"
            f"{document_text[:8000]}\n"
            "Return a JSON array of concepts with fields: concept_title (string), concept_explanation (string), "
            "source_page_number (integer or null), source_video_timestamp_start_seconds (integer or null), "
            "source_video_timestamp_end_seconds (integer or null)."
        )
        response = mistral_client.chat(
            model="mixtral-8x7b-instruct-v0.1",
            messages=[ChatMessage(role="user", content=prompt)],
            max_tokens=1000
        )
        if response.choices and response.choices[0].message.content:
            concepts = parse_llm_json(response.choices[0].message.content)
            valid_concepts = []
            for concept in concepts:
                try:
                    standardized = {
                        "concept_title": concept.get("concept_title", "Untitled Concept"),
                        "concept_explanation": concept.get("concept_explanation", ""),
                        "source_page_number": concept.get("source_page_number", None),
                        "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds", None),
                        "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds", None)
                    }
                    KeyConcept(**standardized)
                    valid_concepts.append(standardized)
                except ValidationError as ve:
                    logger.warning(f"Invalid concept format: {ve}. Concept: {concept}")
                    continue
            logger.info(f"Mistral fallback extracted {len(valid_concepts)} concepts")
            return valid_concepts
        logger.warning("No valid response from Mistral")
        return []
    except Exception as e:
        logger.error(f"Error with Mistral fallback: {e}")
        return []

def generate_key_concepts_dspy(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[dict]:
    """Generates key concepts using DSPy with fallbacks."""
    if not gemini_lm:
        logger.warning("DSPy Google LM not configured. Falling back to direct Google GenAI.")
        concepts = generate_key_concepts_direct_google(document_text, language, comprehension_level, is_video)
        if concepts:
            return concepts
        logger.warning("Direct Google GenAI failed. Falling back to Mistral.")
        return generate_key_concepts_mistral_fallback(document_text, language, comprehension_level, is_video)

    max_chunk_size = 8000
    overlap = 2000
    
    if len(document_text) > max_chunk_size:
        logger.info(f"Document is long ({len(document_text)} chars), processing in chunks with {overlap} char overlap")
        chunks = [document_text[i:i + max_chunk_size] for i in range(0, len(document_text), max_chunk_size - overlap)]
        
        all_concepts = []
        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            chunk_concepts = _extract_key_concepts_from_chunk(chunk, language, comprehension_level, is_video, f"Chunk {i+1}/{len(chunks)}")
            all_concepts.extend(chunk_concepts)
        
        deduplicated_concepts = _deduplicate_concepts(all_concepts)
        validated_concepts = _validate_references(deduplicated_concepts, document_text, is_video)
        return _standardize_concept_format(validated_concepts, is_video)
    
    concepts = _extract_key_concepts_from_chunk(document_text, language, comprehension_level, is_video)
    validated_concepts = _validate_references(concepts, document_text, is_video)
    return _standardize_concept_format(validated_concepts, is_video)

def _extract_key_concepts_from_chunk(document_chunk: str, language: str, comprehension_level: str, is_video: bool, chunk_info: str = "") -> List[dict]:
    """Extract key concepts from a document chunk with retries."""
    max_retries = 3
    temperatures = [0.1, 0.3, 0.5]

    content_instruction = (
        f"Extract ALL important concepts from this {'video transcript' if is_video else 'PDF document'} for a {comprehension_level} audience in {language}. "
        f"For each concept, provide: "
        f"1. concept_title (string): Concise title. "
        f"2. concept_explanation (string): Clear explanation. "
        f"3. source_page_number (integer or null): Page number for PDFs. "
        f"4. source_video_timestamp_start_seconds (integer or null): Start timestamp for videos. "
        f"5. source_video_timestamp_end_seconds (integer or null): End timestamp for videos. "
        f"Return a valid JSON array with no markdown or extra text. Example: "
        f'[{{"concept_title": "Example", "concept_explanation": "Description", "source_page_number": 5, '
        f'"source_video_timestamp_start_seconds": null, "source_video_timestamp_end_seconds": null}}]'
    )

    for attempt, temperature in enumerate(temperatures, 1):
        try:
            logger.info(f"Extracting concepts from {chunk_info or 'document'} {'(video)' if is_video else '(text)'} "
                        f"(attempt {attempt}/{max_retries}, temperature={temperature}, provider=google_genai)")
            
            with dspy.context(lm=gemini_lm, temperature=temperature):
                response = key_concept_extractor(
                    document_content=document_chunk,
                    document_type="video transcript" if is_video else "PDF document",
                    content_instruction=content_instruction
                )
            
            if not response or not hasattr(response, 'key_concepts_json') or not response.key_concepts_json:
                logger.warning(f"No key_concepts_json in response on attempt {attempt}")
                continue
            
            parsed_concepts = parse_llm_json(response.key_concepts_json)
            if not parsed_concepts:
                logger.warning(f"Failed to parse JSON on attempt {attempt}")
                continue
            
            valid_concepts = []
            for concept in parsed_concepts:
                try:
                    standardized = {
                        "concept_title": concept.get("concept_title", "Untitled Concept"),
                        "concept_explanation": concept.get("concept_explanation", ""),
                        "source_page_number": concept.get("source_page_number", None),
                        "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds", None),
                        "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds", None)
                    }
                    KeyConcept(**standardized)
                    valid_concepts.append(standardized)
                except ValidationError as ve:
                    logger.warning(f"Invalid concept format: {ve}. Concept: {concept}")
                    continue
            
            if valid_concepts:
                logger.info(f"Extracted {len(valid_concepts)} valid concepts on attempt {attempt}")
                return valid_concepts
            logger.warning(f"No valid concepts on attempt {attempt}")
        
        except Exception as e:
            logger.error(f"Error extracting key concepts on attempt {attempt}: {e}")
            if attempt == max_retries:
                logger.warning("DSPy failed after all retries. Falling back to direct Google GenAI.")
                concepts = generate_key_concepts_direct_google(document_chunk, language, comprehension_level, is_video)
                if concepts:
                    return concepts
                logger.warning("Direct Google GenAI failed. Falling back to Mistral.")
                return generate_key_concepts_mistral_fallback(document_chunk, language, comprehension_level, is_video)
        
        if attempt < max_retries:
            time.sleep(BASE_DELAY * (2 ** attempt))
    
    logger.error(f"Failed to extract concepts after {max_retries} attempts")
    return []

def _deduplicate_concepts(concepts: List[dict]) -> List[dict]:
    """Deduplicate concepts based on title similarity."""
    if not concepts:
        return []
    
    unique_concepts = []
    seen_concepts = set()
    
    for concept in concepts:
        concept_title = concept.get("concept_title", "").lower().strip()
        if not concept_title or any(similar_enough(concept_title, seen) for seen in seen_concepts):
            continue
        seen_concepts.add(concept_title)
        unique_concepts.append(concept)
        logger.debug(f"Keeping unique concept: '{concept_title[:50]}...'")
    
    return unique_concepts

def similar_enough(str1: str, str2: str) -> bool:
    """Check if two strings are similar enough to be duplicates."""
    if str1 in str2 or str2 in str1:
        return True
    
    words1 = set(str1.lower().split())
    words2 = set(str2.lower().split())
    
    if not words1 or not words2:
        return False
    
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    similarity = intersection / union if union > 0 else 0
    return similarity > 0.7

def _standardize_concept_format(concepts: List[dict], is_video: bool = False) -> List[dict]:
    """Standardize concept format."""
    if not concepts:
        return []
    
    standardized_concepts = []
    for concept in concepts:
        if not concept:
            continue
        standardized = {
            "concept_title": concept.get("concept_title", "Untitled Concept"),
            "concept_explanation": concept.get("concept_explanation", ""),
            "source_page_number": concept.get("source_page_number", None),
            "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds", None),
            "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds", None)
        }
        if is_video:
            standardized["source_page_number"] = None
        else:
            standardized["source_video_timestamp_start_seconds"] = None
            standardized["source_video_timestamp_end_seconds"] = None
        try:
            KeyConcept(**standardized)
            standardized_concepts.append(standardized)
            logger.debug(f"Standardized concept: {standardized['concept_title'][:50]}...")
        except ValidationError as ve:
            logger.warning(f"Invalid concept after standardization: {ve}. Concept: {standardized}")
    
    logger.info(f"Standardized {len(standardized_concepts)} concepts")
    return standardized_concepts

def _validate_references(concepts: List[dict], document_text: str, is_video: bool) -> List[dict]:
    """Validate and correct timestamps or page references with improved metadata cross-referencing."""
    validated_concepts = []
    
    if is_video:
        # Extract all valid timestamp ranges from the document
        all_timestamps = re.findall(r'\[(\d+:\d+)\s*-\s*(\d+:\d+)\]', document_text)
        valid_timestamp_ranges = []
        for start_time, end_time in all_timestamps:
            try:
                start_mins, start_secs = map(int, start_time.split(':'))
                end_mins, end_secs = map(int, end_time.split(':'))
                start_seconds = start_mins * 60 + start_secs
                end_seconds = end_mins * 60 + end_secs
                if start_seconds < end_seconds:  # Ensure valid range
                    valid_timestamp_ranges.append((start_seconds, end_seconds, f"{start_time} - {end_time}"))
            except ValueError:
                continue
        
        # Also extract standalone timestamps
        standalone_timestamps = re.findall(r'\b(\d+:\d+)\b', document_text)
        for timestamp in standalone_timestamps:
            try:
                mins, secs = map(int, timestamp.split(':'))
                seconds = mins * 60 + secs
                valid_timestamp_ranges.append((seconds, seconds + 30, timestamp))  # Assume 30s duration
            except ValueError:
                continue
        
        for concept in concepts:
            start_seconds = concept.get('source_video_timestamp_start_seconds')
            end_seconds = concept.get('source_video_timestamp_end_seconds')
            found_valid_timestamp = False
            
            for valid_start, valid_end, timestamp_str in valid_timestamp_ranges:
                if (start_seconds is None or
                    (valid_start <= start_seconds <= valid_end) or
                    (valid_start <= end_seconds <= valid_end) or
                    (start_seconds <= valid_start and end_seconds >= valid_end)):
                    concept['source_video_timestamp_start_seconds'] = valid_start
                    concept['source_video_timestamp_end_seconds'] = valid_end
                    concept['source_timestamp'] = timestamp_str
                    found_valid_timestamp = True
                    break
            
            if not found_valid_timestamp and valid_timestamp_ranges:
                # Assign the closest timestamp
                closest_start, closest_end, closest_str = min(valid_timestamp_ranges, key=lambda x: abs(x[0] - (start_seconds or 0)))
                concept['source_video_timestamp_start_seconds'] = closest_start
                concept['source_video_timestamp_end_seconds'] = closest_end
                concept['source_timestamp'] = closest_str
                logger.warning(f"Assigned closest timestamp to concept: {concept.get('concept_title')}")
            
            validated_concepts.append(concept)
    else:
        # Extract all valid page numbers from the document
        all_pages = re.findall(r'Page\s+(\d+)', document_text)
        valid_pages = sorted(set(int(page) for page in all_pages if int(page) > 0))
        
        for concept in concepts:
            source_page = concept.get('source_page_number')
            if isinstance(source_page, str) and '-' in source_page:
                pages = [int(p) for p in source_page.split('-') if p.isdigit()]
            else:
                pages = [source_page] if isinstance(source_page, int) else []
            
            valid_concept_pages = [p for p in pages if p in valid_pages]
            if not valid_concept_pages and valid_pages:
                # Assign the closest page number
                closest_page = min(valid_pages, key=lambda x: abs(x - (source_page or 0)))
                concept['source_page_number'] = closest_page
                logger.warning(f"Assigned closest page to concept: {concept.get('concept_title')}")
            elif valid_concept_pages:
                concept['source_page_number'] = valid_concept_pages[0]
            
            validated_concepts.append(concept)
    
    logger.info(f"Validated references for {len(validated_concepts)} concepts")
    return validated_concepts

def generate_summary_dspy(text_to_summarize: str, language: str = "English", comprehension_level: str = "Beginner") -> str:
    """Generates a summary using DSPy."""
    if not gemini_lm:
        logger.error("DSPy Google LM not configured.")
        return "Error: LLM service not configured."
    
    try:
        with dspy.context(lm=gemini_lm):
            response = summary_predictor(
                document_text=text_to_summarize[:MAX_TOKENS_CONTEXT],
                language=language,
                comprehension_level=comprehension_level
            )
            if response and hasattr(response, 'summary') and response.summary:
                logger.info("Successfully generated summary.")
                return response.summary
            logger.warning("DSPy returned empty or invalid summary.")
            return ""
    except Exception as e:
        logger.error(f"Error generating summary with DSPy: {e}")
        return ""

def generate_explanation_dspy(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_length: int = 8000) -> str:
    """Generates an explanation using DSPy."""
    if not text_chunk:
        logger.warning("generate_explanation_dspy called with empty text_chunk.")
        return ""
    
    if not gemini_lm:
        logger.error("DSPy Google LM not configured.")
        return "Error: LLM service not configured."
    
    try:
        truncated_chunk = text_chunk[:max_context_length]
        with dspy.context(lm=gemini_lm):
            response = explain_predictor(
                context=truncated_chunk,
                language=language,
                comprehension_level=comprehension_level
            )
            if response and hasattr(response, 'explanation') and response.explanation:
                logger.debug(f"DSPy generated explanation: {response.explanation[:50]}...")
                return response.explanation
            logger.warning(f"DSPy returned empty or invalid explanation for chunk: {truncated_chunk[:50]}...")
            return ""
    except Exception as e:
        logger.error(f"Error generating explanation with DSPy: {e}")
        return ""

def map_concept_to_segments(concept: dict, segments: List[dict], min_segments: int = 1, max_segments: int = 3) -> dict:
    """Maps a concept to video transcript segments."""
    if not concept or not segments or len(segments) == 0:
        logger.warning("Cannot map concept to segments: empty input")
        return concept
    
    concept_text = f"{concept.get('concept_title', '')} {concept.get('concept_explanation', '')}".strip()
    if not concept_text:
        logger.warning("Empty concept text, cannot map to segments")
        return concept
    
    try:
        concept_embedding = get_text_embedding([concept_text])[0]
        segment_texts = [segment.get('content', '') for segment in segments]
        segment_embeddings = get_text_embeddings_in_batches(segment_texts) if segment_texts else []
        
        similarities = []
        for i, seg_embedding in enumerate(segment_embeddings):
            if seg_embedding and concept_embedding:
                similarity = np.dot(concept_embedding, seg_embedding) / (
                    np.linalg.norm(concept_embedding) * np.linalg.norm(seg_embedding))
                similarities.append((i, similarity))
        
        similarities.sort(key=lambda x: x[1], reverse=True)
        num_segments_to_use = min(max(min_segments, len(similarities) // 10), max_segments)
        best_segments = similarities[:num_segments_to_use] if num_segments_to_use > 0 else []
        
        if best_segments:
            sorted_indices = sorted([idx for idx, _ in best_segments])
            start_time = segments[sorted_indices[0]].get('start_time', 0)
            end_time = segments[sorted_indices[-1]].get('end_time', 0)
            if end_time <= start_time:
                end_time = start_time + 10
            avg_confidence = sum([score for _, score in best_segments]) / len(best_segments)
            
            concept['source_video_timestamp_start_seconds'] = start_time
            concept['source_video_timestamp_end_seconds'] = end_time
            concept['confidence_score'] = float(avg_confidence)
            logger.info(f"Mapped concept '{concept.get('concept_title', '')[0:30]}...' to timestamps: "
                        f"{start_time:.2f}s - {end_time:.2f}s (confidence: {avg_confidence:.3f})")
        else:
            concept['source_video_timestamp_start_seconds'] = 0
            concept['source_video_timestamp_end_seconds'] = 0
            concept['confidence_score'] = 0.0
            logger.warning("Could not find matching segments for concept")
    
    except Exception as e:
        logger.error(f"Error mapping concept to segments: {e}")
    
    return concept

def get_text_embedding(inputs: List[str]) -> List[List[float]]:
    """Generate embeddings for a single input or small batch."""
    if not mistral_client:
        logger.error("Mistral client not initialized.")
        return [[] for _ in inputs]
    
    try:
        response = mistral_client.embeddings(model="mistral-embed", input=inputs)
        return [emb.embedding for emb in response.data]
    except Exception as e:
        logger.error(f"Error getting embeddings from Mistral: {e}")
        return [[] for _ in inputs]

def get_text_embeddings_in_batches(inputs: List[str], batch_size: int = 10) -> List[List[float]]:
    """Generate embeddings in batches with rate limiting."""
    if not mistral_client:
        logger.error("Mistral client not initialized.")
        return [[] for _ in inputs]
    
    all_embeddings = []
    max_retries = 5
    base_delay = 5
    max_delay = 120
    min_interval = 3
    last_request_time = 0
    
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        
        current_time = time.time()
        elapsed = current_time - last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        
        retries = 0
        while retries < max_retries:
            try:
                response = mistral_client.embeddings(model="mistral-embed", input=batch)
                all_embeddings.extend([emb.embedding for emb in response.data])
                last_request_time = time.time()
                break
            except Exception as e:
                retries += 1
                error_msg = str(e).lower()
                if "429" in error_msg or "rate limit" in error_msg or "capacity exceeded" in error_msg:
                    if retries < max_retries:
                        delay = min(base_delay * (2 ** retries), max_delay) * random.uniform(0.8, 1.2)
                        logger.warning(f"Rate limit hit for batch {i}, retry {retries}/{max_retries} in {delay:.1f}s")
                        time.sleep(delay)
                        continue
                logger.error(f"Error getting embeddings for batch {i}: {e}")
                all_embeddings.extend([[] for _ in batch])
                break
        
        time.sleep(BASE_DELAY)
    
    return all_embeddings

def token_count(text: str) -> int:
    """Approximate token count using tiktoken."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        return 0

def extract_image_text(base64_image: str) -> str:
    """Extract text from an image using Mistral API."""
    if not mistral_client:
        logger.error("Mistral client not initialized.")
        return ""
    
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {MISTRAL_API_KEY}"}
    data = {
        "model": "pixtral-12b-2409",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What’s in this image?"},
                    {"type": "image_url", "image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]
            }
        ],
        "max_tokens": 1500
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"Error extracting text from image: {e}")
        return ""

if __name__ == "__main__":
    logger.info("Running test...")
    test_text = "Artificial intelligence is the simulation of human intelligence in machines."
    
    concepts = generate_key_concepts_dspy(test_text)
    print(f"Key concepts: {json.dumps(concepts, indent=2)}")
    
    embeddings = get_text_embedding([test_text])
    print(f"Embeddings length: {len(embeddings[0]) if embeddings else 0}")
    
    token_count_result = token_count(test_text)
    print(f"Token count: {token_count_result}")