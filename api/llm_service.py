import logging
import re
import json
import numpy as np
from typing import List, Dict, Any
from nltk.tokenize import sent_tokenize
from mistralai.client import MistralClient
import google.generativeai as genai
import dspy
import nltk
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")

# Initialize clients
genai.configure(api_key=google_api_key)
mistral_client = MistralClient(api_key=mistral_key) if mistral_key else None

# Download NLTK data
nltk.download('punkt', quiet=True)

logger = logging.getLogger(__name__)

# DSPy Configuration
gemini_lm = dspy.Google(model="gemini-1.5-flash", api_key=google_api_key, max_output_tokens=2048) if google_api_key else None
if gemini_lm:
    dspy.settings.configure(lm=gemini_lm)

class KeyConceptSignature(dspy.Signature):
    """Extract key concepts with detailed explanations from a document."""
    document_text = dspy.InputField(desc="The full text of the document")
    language = dspy.InputField(desc="Language for the concepts (e.g., English)")
    comprehension_level = dspy.InputField(desc="Comprehension level (e.g., Beginner)")
    concepts = dspy.OutputField(desc="List of key concepts with title, detailed explanation, and source page/timestamp")

def chunk_text(text: str, max_chunks_per_page: int = 5) -> List[Dict[str, Any]]:
    """
    Chunk text into segments suitable for QA and learning material generation.
    Args:
        text: Input text (PDF text with "Page N" markers or YouTube transcript with "[MM:SS]" timestamps).
        max_chunks_per_page: Maximum chunks per page for PDFs (default: 5).
    Returns:
        List of dictionaries with chunk content and metadata (page number or timestamps).
    """
    try:
        logger.debug("Chunking text...")
        chunks = []
        max_tokens = 1000
        min_tokens = 200
        
        def count_tokens(content: str) -> int:
            return int(len(content.split()) * 1.5)

        is_youtube = bool(re.search(r'\[\d{2}:\d{2}(?::\d{2})?\]', text))
        if is_youtube:
            segments = re.split(r'(\[\d{2}:\d{2}(?::\d{2})?\])', text)
            current_chunk = {"content": "", "metadata": {"start_time": None, "end_time": None, "doc_type": "youtube"}}
            current_tokens = 0
            
            for i in range(1, len(segments), 2):
                timestamp = segments[i].strip('[]')
                segment_text = segments[i + 1].strip()
                if not segment_text:
                    continue
                
                segment_tokens = count_tokens(segment_text)
                if current_tokens + segment_tokens > max_tokens and current_tokens >= min_tokens:
                    chunks.append(current_chunk)
                    current_chunk = {"content": "", "metadata": {"start_time": timestamp, "end_time": None, "doc_type": "youtube"}}
                    current_tokens = 0
                
                current_chunk["content"] += f"{segment_text} "
                current_chunk["metadata"]["end_time"] = timestamp
                current_tokens += segment_tokens
            
            if current_chunk["content"].strip() and current_tokens >= min_tokens:
                chunks.append(current_chunk)
        
        else:
            pages = re.split(r'(Page \d+\n)', text)
            current_chunk = {"content": "", "metadata": {"page_number": None, "doc_type": "pdf"}}
            current_tokens = 0
            chunks_per_page = 0
            
            for i in range(0, len(pages), 2):
                page_marker = pages[i] if i < len(pages) else ""
                page_text = pages[i + 1] if i + 1 < len(pages) else ""
                if not page_text.strip():
                    continue
                
                page_num_match = re.match(r'Page (\d+)', page_marker)
                page_num = int(page_num_match.group(1)) if page_num_match else None
                chunks_per_page = 0
                
                sentences = sent_tokenize(page_text)
                for sentence in sentences:
                    sentence_tokens = count_tokens(sentence)
                    if chunks_per_page >= max_chunks_per_page:
                        if current_chunk["content"].strip() and current_tokens >= min_tokens:
                            chunks.append(current_chunk)
                        current_chunk = {"content": "", "metadata": {"page_number": page_num, "doc_type": "pdf"}}
                        current_tokens = 0
                        chunks_per_page = 0
                    
                    if current_tokens + sentence_tokens > max_tokens and current_tokens >= min_tokens:
                        chunks.append(current_chunk)
                        current_chunk = {"content": "", "metadata": {"page_number": page_num, "doc_type": "pdf"}}
                        current_tokens = 0
                        chunks_per_page += 1
                    
                    current_chunk["content"] += f"{sentence} "
                    current_chunk["metadata"]["page_number"] = page_num
                    current_tokens += sentence_tokens
                
                if current_chunk["content"].strip() and current_tokens >= min_tokens and chunks_per_page < max_chunks_per_page:
                    chunks.append(current_chunk)
                    current_chunk = {"content": "", "metadata": {"page_number": None, "doc_type": "pdf"}}
                    current_tokens = 0
                    chunks_per_page += 1
            
            if current_chunk["content"].strip() and current_tokens >= min_tokens:
                chunks.append(current_chunk)
        
        logger.info(f"Successfully chunked text into {len(chunks)} parts")
        return chunks
    except Exception as e:
        logger.error(f"Error chunking text: {e}")
        raise

def generate_key_concepts(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[Dict[str, Any]]:
    """
    Generates key concepts with explanations and source links from document text using Mistral.
    
    Args:
        document_text: The full text of the document.
        language: Target language for the concepts.
        comprehension_level: Target comprehension level.
        is_video: Whether the document is a video transcript.
    
    Returns:
        A list of dictionaries, each representing a key concept with title, explanation, and source.
    """
    if not mistral_client:
        logger.error("Mistral client not configured. Cannot generate key concepts.")
        return []

    max_chunk_size = 8000
    overlap = 2000
    chunks = []
    
    if len(document_text) > max_chunk_size:
        logger.info(f"Document is long ({len(document_text)} chars), processing in chunks with {overlap} char overlap")
        for i in range(0, len(document_text), max_chunk_size - overlap):
            chunk = document_text[i:i + max_chunk_size]
            chunks.append((chunk, i // (max_chunk_size - overlap) + 1))
    else:
        chunks = [(document_text, 1)]

    all_concepts = []
    for chunk, chunk_num in chunks:
        logger.info(f"Processing chunk {chunk_num}/{len(chunks)} ({len(chunk)} chars)")
        chunk_concepts = _extract_key_concepts_from_chunk(chunk, language, comprehension_level, is_video, f"Chunk {chunk_num}/{len(chunks)}")
        all_concepts.extend(chunk_concepts)
    
    deduplicated_concepts = _deduplicate_concepts(all_concepts)
    validated_concepts = _validate_references(deduplicated_concepts, document_text, is_video)
    standardized_concepts = _standardize_concept_format(validated_concepts, is_video)
    return standardized_concepts

def _extract_key_concepts_from_chunk(document_chunk: str, language: str, comprehension_level: str, is_video: bool, chunk_info: str = "") -> List[Dict[str, Any]]:
    """Extract key concepts from a document chunk using Mistral with iterative refinement."""
    try:
        logger.info(f"Extracting concepts from {chunk_info} {'(video)' if is_video else '(text)'}")
        
        # Iterative prompts for refinement
        prompts = [
            (
                f"Extract key concepts from this {'video transcript' if is_video else 'document'} in {language} for a {comprehension_level} audience. "
                f"Provide a concise title, a detailed explanation (2-3 sentences), and the exact {'timestamp range (MM:SS - MM:SS)' if is_video else 'page number'} where the concept is discussed. "
                f"Focus on definitions and core ideas. Return as a JSON array."
            ),
            (
                f"Extract key concepts from this {'video transcript' if is_video else 'document'} in {language} for a {comprehension_level} audience. "
                f"Provide a concise title, a detailed explanation (2-3 sentences), and the exact {'timestamp range (MM:SS - MM:SS)' if is_video else 'page number'}. "
                f"Focus on implications and applications. Return as a JSON array."
            ),
            (
                f"Extract key concepts from this {'video transcript' if is_video else 'document'} in {language} for a {comprehension_level} audience. "
                f"Provide a concise title, a detailed explanation (2-3 sentences), and the exact {'timestamp range (MM:SS - MM:SS)' if is_video else 'page number'}. "
                f"Focus on examples and practical insights. Return as a JSON array."
            )
        ]
        
        all_concepts = []
        for i, prompt in enumerate(prompts):
            logger.debug(f"Applying prompt {i+1}/{len(prompts)} for chunk: {document_chunk[:50]}...")
            response = mistral_client.chat(
                model="mixtral-8x7b",
                messages=[{"role": "user", "content": prompt + f"\nContent: {document_chunk[:8000]}" }],
                max_tokens=1000
            )
            raw_json = response.choices[0].message.content.strip()
            match = re.search(r"```(?:json)?\n(.*)\n```", raw_json, re.DOTALL)
            json_text = match.group(1).strip() if match else raw_json
            
            try:
                concepts = json.loads(json_text)
                if not isinstance(concepts, list):
                    logger.warning(f"Prompt {i+1} returned non-list JSON: {json_text[:200]}")
                    continue
                for concept in concepts:
                    concept["iteration"] = i + 1  # Track which prompt generated this
                all_concepts.extend(concepts)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from prompt {i+1}: {e}")
                continue
        
        # Filter concepts with valid sources
        filtered_concepts = [
            c for c in all_concepts
            if (c.get("source_page_number") is not None or
                (c.get("source_video_timestamp_start_seconds") is not None and
                 c.get("source_video_timestamp_end_seconds") is not None))
        ]
        logger.info(f"Extracted {len(filtered_concepts)} valid concepts from chunk")
        return filtered_concepts
    except Exception as e:
        logger.error(f"Error extracting key concepts: {e}")
        return []

def _deduplicate_concepts(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate concepts based on title and explanation similarity using Mistral embeddings."""
    if not concepts:
        return []
    
    if not mistral_client:
        logger.warning("Mistral client not initialized, using basic deduplication")
        return _deduplicate_concepts_basic(concepts)
    
    unique_concepts = []
    seen_embeddings = []
    
    for concept in concepts:
        concept_text = f"{concept.get('concept_title', '')} {concept.get('concept_explanation', '')}".strip()
        if not concept_text:
            continue
        
        embedding = get_text_embedding(concept_text)
        if not embedding:
            unique_concepts.append(concept)
            continue
        
        is_unique = True
        for seen_emb in seen_embeddings:
            similarity = np.dot(embedding, seen_emb) / (np.linalg.norm(embedding) * np.linalg.norm(seen_emb))
            if similarity > 0.9:
                is_unique = False
                break
        
        if is_unique:
            seen_embeddings.append(embedding)
            unique_concepts.append(concept)
    
    logger.info(f"Deduplicated {len(concepts)} concepts to {len(unique_concepts)}")
    return unique_concepts

def _deduplicate_concepts_basic(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback deduplication using string similarity."""
    unique_concepts = []
    seen_concepts = set()
    
    for concept in concepts:
        concept_title = concept.get("concept_title", concept.get("concept", "")).lower().strip()
        if not concept_title or any(similar_enough(concept_title, seen) for seen in seen_concepts):
            continue
        seen_concepts.add(concept_title)
        unique_concepts.append(concept)
    
    return unique_concepts

def _validate_references(concepts: List[Dict[str, Any]], document_text: str, is_video: bool) -> List[Dict[str, Any]]:
    """Validate and correct timestamps or page references, rejecting concepts without valid sources."""
    validated_concepts = []
    
    if is_video:
        timestamp_pattern = r'\[(\d+:\d+)\s*-\s*(\d+:\d+)\]'
        valid_timestamps = re.findall(timestamp_pattern, document_text)
        valid_timestamp_ranges = []
        for start_time, end_time in valid_timestamps:
            try:
                start_mins, start_secs = map(int, start_time.split(':'))
                end_mins, end_secs = map(int, end_time.split(':'))
                start_seconds = start_mins * 60 + start_secs
                end_seconds = end_mins * 60 + end_secs
                valid_timestamp_ranges.append((start_seconds, end_seconds, f"{start_time} - {end_time}"))
            except ValueError:
                continue
        
        for concept in concepts:
            start_seconds = concept.get('source_video_timestamp_start_seconds')
            end_seconds = concept.get('source_video_timestamp_end_seconds')
            if start_seconds is None or end_seconds is None:
                logger.warning(f"Rejecting concept without valid timestamps: {concept.get('concept_title')}")
                continue
            
            found_valid = False
            for valid_start, valid_end, timestamp_str in valid_timestamp_ranges:
                if (valid_start <= start_seconds <= valid_end or
                    valid_start <= end_seconds <= valid_end or
                    (start_seconds <= valid_start and end_seconds >= valid_end)):
                    concept['source_video_timestamp_start_seconds'] = valid_start
                    concept['source_video_timestamp_end_seconds'] = valid_end
                    concept['source_video_timestamp'] = timestamp_str
                    validated_concepts.append(concept)
                    found_valid = True
                    break
            
            if not found_valid:
                logger.warning(f"Rejecting concept with invalid timestamps: {concept.get('concept_title')}")
    
    else:
        valid_pages = [int(p) for p in re.findall(r'Page\s+(\d+)', document_text)]
        for concept in concepts:
            page = concept.get('source_page_number')
            if page is None or (valid_pages and int(page) not in valid_pages):
                logger.warning(f"Rejecting concept without valid page number: {concept.get('concept_title')}")
                continue
            validated_concepts.append(concept)
    
    logger.info(f"Validated {len(validated_concepts)} concepts with sources")
    return validated_concepts

def _standardize_concept_format(concepts: List[Dict[str, Any]], is_video: bool) -> List[Dict[str, Any]]:
    """Standardize concept format to use consistent field names."""
    standardized_concepts = []
    for concept in concepts:
        if not concept:
            continue
        standardized = {
            'concept_title': concept.get('concept_title', concept.get('concept', 'Untitled Concept')),
            'concept_explanation': concept.get('concept_explanation', concept.get('explanation', ''))
        }
        if is_video:
            standardized['source_video_timestamp_start_seconds'] = concept.get('source_video_timestamp_start_seconds', 0)
            standardized['source_video_timestamp_end_seconds'] = concept.get('source_video_timestamp_end_seconds', 0)
            standardized['source_video_timestamp'] = concept.get('source_video_timestamp', '')
        else:
            standardized['source_page_number'] = concept.get('source_page_number', concept.get('source_page', None))
        standardized_concepts.append(standardized)
    logger.info(f"Standardized {len(standardized_concepts)} concepts")
    return standardized_concepts

def get_text_embedding(text: str) -> List[float]:
    if not mistral_client:
        logger.error("Mistral client not initialized")
        return []
    try:
        response = mistral_client.embeddings(model="mistral-embed", input=[text])
        return response.data[0].embedding if response.data else []
    except Exception as e:
        logger.error(f"Error getting embedding: {e}")
        return []

def get_text_embeddings_in_batches(inputs: List[str], batch_size: int = 10) -> List[List[float]]:
    if not mistral_client:
        logger.error("Mistral client not initialized")
        return []
    all_embeddings = []
    max_retries = 5
    base_delay = 2
    
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        retries = 0
        while retries < max_retries:
            try:
                response = mistral_client.embeddings(model="mistral-embed", input=batch)
                all_embeddings.extend([emb.embedding for emb in response.data])
                break
            except Exception as e:
                retries += 1
                if retries < max_retries:
                    delay = base_delay * (2 ** retries)
                    logger.warning(f"Retrying embedding batch {i} in {delay:.1f}s: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"Failed embedding batch {i}: {e}")
                    all_embeddings.extend([[] for _ in batch])
        time.sleep(1)
    
    return all_embeddings