import logging
import re
import json
import numpy as np
from typing import List, Dict, Any
import dspy
import requests
import time
import os
import gc
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
MODEL_ACCESS_KEY = os.getenv("MODEL_ACCESS_KEY")
INFERENCE_BASE_URL = os.getenv("INFERENCE_BASE_URL", "https://inference.do-ai.run/v1")
DO_EMBEDDINGS_URL = os.getenv("DO_EMBEDDINGS_URL")
MODEL_EMBEDDING_ID = os.getenv("MODEL_EMBEDDING_ID", "multi-qa-mpnet-base-dot-v1")
# Support separate API keys for embeddings (optional - falls back to MODEL_ACCESS_KEY)
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or MODEL_ACCESS_KEY

logger = logging.getLogger(__name__)

# Active models
CHAT_MODEL = os.getenv("MODEL_CHAT_ID", "openai-gpt-oss-20b")

# Max tokens allowed for combined context in syntext_agent
try:
    MAX_TOKENS_CONTEXT = int(os.getenv("MAX_TOKENS_CONTEXT", "120000"))
except ValueError:
    MAX_TOKENS_CONTEXT = 120000


def gradient_chat(prompt: str, max_tokens: int = 800) -> str:
    """Generate text using OpenAI-compatible chat completions over HTTP."""
    if not MODEL_ACCESS_KEY:
        logger.error("MODEL_ACCESS_KEY not configured for chat")
        return ""

    url = f"{INFERENCE_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
    }
    data = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }

    last_err: Exception | None = None
    delay = 1
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            body = resp.json()

            choices = body.get("choices") or []
            first = choices[0] if choices else {}

            # OpenAI-style chat
            content = (first.get("message") or {}).get("content")
            # Some servers return text on the choice itself
            if content is None:
                content = first.get("text")
            # Some servers return an array of content parts
            if isinstance(content, list):
                content = "".join([str(p.get("text", "")) for p in content if isinstance(p, dict)])

            if content is None:
                logger.warning(
                    "LLM returned no content. keys=%s choice_keys=%s",
                    list(body.keys()),
                    list(first.keys()) if isinstance(first, dict) else type(first),
                )
                last_err = ValueError("missing_content")
            else:
                content_str = str(content).strip()
                if content_str:
                    return content_str
                last_err = ValueError("empty_content")

        except Exception as e:
            last_err = e

        time.sleep(delay)
        delay *= 2

    logger.error(f"HTTP chat completion error after retries: {last_err}")
    return ""
# DSPy Configuration - Make it completely optional
gemini_lm = None
explain_predictor = None
try:
    # Keep DSPy optional; if you later wire an OpenAI-compatible client in DSPy, configure it here.
    # For now, we avoid hard dependency and use fallback paths if not configured.
    pass
except Exception as e:
    logger.warning(f"DSPy configuration failed: {e}. Using fallback methods.")
    explain_predictor = None

def token_count(content: str, model: str = None) -> int:
    return max(1, int(len(content.split()) * 1.5))
# --- New DSPy-based Explanation Function --- >
def generate_explanation_dspy(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_length: int = 2000) -> str:
    """Generates an explanation for a text chunk using DSPy if configured; falls back otherwise."""
    if not text_chunk:
        logging.warning("generate_explanation_dspy called with empty text_chunk.")
        return ""

    # Truncate context if necessary (DSPy might handle this, but belt-and-suspenders)
    truncated_chunk = text_chunk[:max_context_length]

    try:
        # Check if DSPy is available and configured
        if not explain_predictor:
            logging.warning("DSPy explanation predictor not available. Using fallback explanation.")
            return f"This section discusses {truncated_chunk[:100]}... (Explanation generated via fallback method)"

        response = explain_predictor(context=truncated_chunk, language=language, comprehension_level=comprehension_level)
        if response and hasattr(response, 'explanation') and response.explanation:
             logging.debug(f"DSPy generated explanation: {response.explanation[:50]}...")
             return response.explanation
        else:
            logging.warning(f"DSPy predictor returned empty or invalid response for chunk: {truncated_chunk[:50]}...")
            return f"This section covers key concepts related to: {truncated_chunk[:100]}... (Fallback explanation)"

    except Exception as e:
        logging.error(f"Error generating explanation with DSPy: {e}", exc_info=True)
        # Return a fallback explanation instead of failing
        return f"This section discusses important concepts in {language}. The content focuses on {truncated_chunk[:100]}... (Explanation generated via fallback method)"

# --- Key concept extraction ---
def generate_key_concepts(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[Dict[str, Any]]:
    """
    Extract key concepts from a document or video transcript.
    Uses sliding window for large documents to improve JSON parsing success.
    """
    if not document_text.strip():
        return []

    all_concepts = []
    doc_length = len(document_text)
    logger.info(f"Processing document ({doc_length} chars) for key concepts")

    # Use sliding window for large documents to improve LLM parsing
    MAX_CHUNK_SIZE = 4000  # Smaller chunks = better JSON parsing
    OVERLAP = 500  # Overlap to avoid losing context at boundaries

    if doc_length <= MAX_CHUNK_SIZE:
        # Process as single chunk
        concepts = _extract_key_concepts_from_chunk(document_text, language, comprehension_level, is_video, "Full Document")
        all_concepts.extend(concepts)
    else:
        # Split into overlapping windows
        chunk_num = 0
        for start in range(0, doc_length, MAX_CHUNK_SIZE - OVERLAP):
            end = min(start + MAX_CHUNK_SIZE, doc_length)
            chunk = document_text[start:end]
            chunk_num += 1
            
            logger.debug(f"Processing chunk {chunk_num} (chars {start}-{end})")
            concepts = _extract_key_concepts_from_chunk(chunk, language, comprehension_level, is_video, f"Chunk {chunk_num}")
            all_concepts.extend(concepts)
            
            if end >= doc_length:
                break

    logger.info(f"Extracted {len(all_concepts)} raw concepts")

    all_concepts = _deduplicate_concepts(all_concepts)
    logger.info(f"After deduplication: {len(all_concepts)} concepts")

    all_concepts = _validate_references(all_concepts, document_text, is_video)
    logger.info(f"After validation: {len(all_concepts)} concepts")

    all_concepts = _standardize_concept_format(all_concepts, is_video)
    logger.info(f"Final: {len(all_concepts)} concepts")

    # Ensure minimum viable concepts
    if len(all_concepts) < 3:
        logger.warning(f"Only {len(all_concepts)} concepts extracted. Adding supplementary concepts.")
        all_concepts.extend(_extract_supplementary_concepts(document_text, is_video, 3 - len(all_concepts)))

    # Final cleanup pass to avoid leaking fallback artifacts into UI/MCQ generation
    all_concepts = _sanitize_concepts(all_concepts)

    return all_concepts


def _sanitize_text_value(value: Any) -> str:
    """Normalize concept fields to remove common transcript artifacts and messy whitespace."""
    if value is None:
        return ""
    text = str(value)
    # Remove bracketed numeric ranges like [155-159]
    text = re.sub(r"\[\s*\d+\s*-\s*\d+\s*\]", " ", text)
    # Normalize newlines/tabs to spaces
    text = re.sub(r"[\r\n\t]+", " ", text)
    # Remove common markdown-ish noise that sometimes appears
    text = text.replace("**", "").replace("`", "")
    # Collapse repeated whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _sanitize_concepts(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize concept_title / concept_explanation for all concept dicts."""
    cleaned: List[Dict[str, Any]] = []
    for c in concepts or []:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        c["concept_title"] = _sanitize_text_value(c.get("concept_title", c.get("concept", "")))
        c["concept_explanation"] = _sanitize_text_value(c.get("concept_explanation", c.get("explanation", "")))
        cleaned.append(c)
    return cleaned

def _extract_key_concepts_from_chunk(
    document_chunk: str,
    language: str,
    comprehension_level: str,
    is_video: bool,
    chunk_info: str = "",
    max_retries: int = 3
) -> List[Dict[str, Any]]:
    """Extracts key concepts with strict JSON validation, retries, and fallbacks."""
    try:
        if not document_chunk.strip():
            logger.warning("Empty document chunk provided.")
            return []

        # --- Dynamic config based on type ---
        if is_video:
            example_json = (
                '[{"concept_title": "Network Latency", '
                '"concept_explanation": "Delay in data transmission affecting transaction speed.", '
                '"source_video_timestamp_start_seconds": 135, '
                '"source_video_timestamp_end_seconds": 180, '
                '"source_video_timestamp": "02:15 - 03:00"}]'
            )
            source_field = "source_video_timestamp"
            content_label = "video transcript"
            source_label = "timestamp (MM:SS - MM:SS format)"
        else:
            example_json = (
                '[{"concept_title": "Distributed Ledger", '
                '"concept_explanation": "A replicated database maintained across multiple nodes.", '
                '"source_page_number": 5}]'
            )
            source_field = "source_page_number"
            content_label = "document"
            source_label = "page number"

        # --- JSON Schema for structured output ---
        if is_video:
            json_schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "concept_title",
                        "concept_explanation",
                        "source_video_timestamp_start_seconds",
                        "source_video_timestamp_end_seconds",
                    ],
                    "properties": {
                        "concept_title": {"type": "string", "minLength": 3, "maxLength": 100},
                        "concept_explanation": {"type": "string", "minLength": 10},
                        "source_video_timestamp_start_seconds": {"type": ["integer", "number"], "minimum": 0},
                        "source_video_timestamp_end_seconds": {"type": ["integer", "number"], "minimum": 0},
                        "source_video_timestamp": {"type": "string"},
                    },
                },
            }
        else:
            json_schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["concept_title", "concept_explanation", "source_page_number"],
                    "properties": {
                        "concept_title": {"type": "string", "minLength": 3, "maxLength": 100},
                        "concept_explanation": {"type": "string", "minLength": 10},
                        "source_page_number": {"type": "integer", "minimum": 1},
                    },
                },
            }

        # --- Prompt template with strict JSON constraints ---
        prompt_template = f"""
You are an expert educator. Extract 5-8 key concepts from this {content_label}.
Write in {language} for {comprehension_level} level.

RULES:
1. Title must be descriptive (e.g., "Neural Networks", "Cloud Computing")
2. NO markdown, NO numbers like "01", NO timestamps like "00:30" as titles
3. Explanation must be 2-3 clear sentences
4. Output ONLY a JSON array, nothing else
5. You MUST include a valid {source_label} field for every concept.
   - For PDFs: choose the page number from the provided "Page N:" markers.
   - For videos: choose start/end seconds from the provided transcript markers.

JSON Schema:
{json.dumps(json_schema, indent=2)}

Example (copy this structure exactly):
{example_json}

Content:
{{chunk}}

JSON array:
""".strip()

        # --- Retry with exponential backoff ---
        delay = 2
        for attempt in range(1, max_retries + 1):
            logger.debug(f"Attempt {attempt}/{max_retries} extracting concepts from chunk {chunk_info}")

            # Truncate to reasonable size for LLM context.
            # If the provider is returning empty content, shrink the chunk and the prompt on retries.
            if attempt == 1:
                max_chunk_chars = 5000
                prompt = prompt_template.replace("{chunk}", document_chunk[:max_chunk_chars])
                max_tokens = 1200
            elif attempt == 2:
                max_chunk_chars = 3000
                compact_prompt = (
                    f"Extract 5-8 key concepts from this {content_label}. "
                    f"Write in {language} for {comprehension_level} level. "
                    f"Output ONLY a JSON array matching this example: {example_json}. "
                    f"Each item must include {source_field}.\n\n"
                    f"Content:\n{document_chunk[:max_chunk_chars]}\n\nJSON array:"
                )
                prompt = compact_prompt
                max_tokens = 1000
            else:
                max_chunk_chars = 2000
                compact_prompt = (
                    f"Return ONLY valid JSON array (no markdown). "
                    f"5-8 concepts. Language={language}. Level={comprehension_level}. "
                    f"Fields: concept_title, concept_explanation, {source_field}. "
                    f"Example: {example_json}.\n\n"
                    f"Content:\n{document_chunk[:max_chunk_chars]}\n\nJSON array:"
                )
                prompt = compact_prompt
                max_tokens = 900
            raw_json = ""

            try:
                raw_json = gradient_chat(prompt, max_tokens=max_tokens)  # Increased for multiple concepts
                logger.debug(f"LLM response length: {len(raw_json)} chars")

            except Exception as e:
                logger.warning(f"Model request failed on attempt {attempt}: {e}")
                time.sleep(delay)
                delay *= 2
                continue

            # --- Sanitize and attempt parse ---
            if not raw_json:
                logger.warning(f"Empty response on attempt {attempt}. Retrying...")
                time.sleep(delay)
                delay *= 2
                continue

            # Strip markdown fences and other common issues
            raw_json = re.sub(r"```(?:json)?\n?", "", raw_json).strip("` \n")
            raw_json = re.sub(r'^\s*//.*?\n', '', raw_json, flags=re.MULTILINE)  # Remove comments
            raw_json = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', raw_json)  # Remove control characters

            # Try parsing JSON
            try:
                concepts = json.loads(raw_json)
                if isinstance(concepts, list):
                    logger.info(f"✅ Successfully parsed JSON on attempt {attempt}")
                    logger.debug(f"Parsed concepts: {concepts}")
                    break
                else:
                    raise ValueError("Parsed JSON is not a list")
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON structure on attempt {attempt}: {e}")
                logger.debug(f"Raw response: {raw_json[:500]}...")
                # Try to extract JSON array substring
                match = re.search(r"\[.*?\]", raw_json, re.DOTALL)
                if match:
                    try:
                        concepts = json.loads(match.group(0))
                        if isinstance(concepts, list):
                            logger.info(f"✅ Extracted valid JSON array on attempt {attempt}")
                            logger.debug(f"Extracted concepts: {concepts}")
                            break
                    except json.JSONDecodeError:
                        pass
                time.sleep(delay)
                delay *= 2
                concepts = []

        else:
            logger.error("All attempts failed to produce valid JSON.")
            concepts = []

        # --- Validate and clean concepts ---
        valid_concepts = []
        for concept in concepts:
            title = _sanitize_text_value(concept.get("concept_title", "")).strip()
            explanation = _sanitize_text_value(concept.get("concept_explanation", "")).strip()
            if (
                title and explanation and
                len(title) >= 3 and
                not title.isdigit() and
                not title.startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')) and
                not re.match(r'^\d{2}:\d{2}', title)
            ):
                concept["concept_title"] = title
                concept["concept_explanation"] = explanation
                valid_concepts.append(concept)
            else:
                logger.warning(f"Skipping invalid concept: title='{title}', explanation='{explanation[:50]}...'")

        # --- Multi-concept fallback extraction ---
        if not valid_concepts and raw_json:
            logger.info("Attempting multi-concept fallback extraction")
            # Try bullet points
            bullets = re.findall(r"[-•*]\s*(.+)", raw_json)
            if bullets:
                for bp in bullets[:8]:  # Limit to 8 concepts
                    parts = bp.strip().split(":", 1)
                    if len(parts) >= 1 and len(parts[0].strip()) >= 3 and not parts[0].strip().isdigit():
                        title = parts[0].strip()[:100]
                        explanation = parts[1].strip() if len(parts) > 1 else bp.strip()
                        valid_concepts.append({
                            "concept_title": title,
                            "concept_explanation": explanation,
                            source_field: None
                        })
            
            # Try numbered list format
            if not valid_concepts:
                numbered = re.findall(r"\d+\.\s*([A-Z][^\n]{10,200})", raw_json)
                if numbered:
                    for concept_text in numbered[:8]:
                        parts = concept_text.split(":", 1)
                        title = parts[0].strip()[:100]
                        explanation = parts[1].strip() if len(parts) > 1 else concept_text.strip()
                        valid_concepts.append({
                            "concept_title": title,
                            "concept_explanation": explanation,
                            source_field: None
                        })

        # Last resort: extract from document text
        if not valid_concepts and len(document_chunk.strip()) > 100:
            logger.warning("Extracting concepts from document text as last resort")
            valid_concepts = _extract_concepts_from_text(document_chunk, is_video, source_field)

        # --- Annotate iteration ---
        for c in valid_concepts:
            c["iteration"] = 1

        logger.debug(f"Extracted {len(valid_concepts)} valid concepts from chunk {chunk_info}")
        return valid_concepts

    except Exception as e:
        logger.error(f"Error extracting key concepts: {e}", exc_info=True)
        return []

# --- Deduplication ---
def _deduplicate_concepts(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not concepts:
        return []

    unique = []
    seen_embeddings = []
    for concept in concepts:
        text = f"{concept.get('concept_title', '')} {concept.get('concept_explanation', '')}".strip()
        if not text:
            continue
        emb = get_text_embedding(text)
        if not emb:
            unique.append(concept)
            continue
        if all(np.dot(emb, e)/(np.linalg.norm(emb)*np.linalg.norm(e)) < 0.9 for e in seen_embeddings):
            seen_embeddings.append(emb)
            unique.append(concept)
    return unique

def _deduplicate_concepts_basic(concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for c in concepts:
        title = c.get("concept_title", c.get("concept", "")).lower().strip()
        if title and title not in seen:
            seen.add(title)
            unique.append(c)
    return unique

# --- Reference validation ---
def _validate_references(concepts: List[Dict[str, Any]], document_text: str, is_video: bool) -> List[Dict[str, Any]]:
    valid = []
    if is_video:
        # For videos, keep concepts even if timestamps are missing (fallback to 0).
        logger.debug(f"Validating {len(concepts)} video concepts")
        for c in concepts:
            start_sec = c.get('source_video_timestamp_start_seconds')
            end_sec = c.get('source_video_timestamp_end_seconds')
            if start_sec is None:
                start_sec = 0
            if end_sec is None:
                end_sec = 0
            c['source_video_timestamp_start_seconds'] = start_sec
            c['source_video_timestamp_end_seconds'] = end_sec
            if not c.get('source_video_timestamp'):
                c['source_video_timestamp'] = f"{int(float(start_sec)//60):02d}:{int(float(start_sec)%60):02d} - {int(float(end_sec)//60):02d}:{int(float(end_sec)%60):02d}"
            valid.append(c)
        logger.debug(f"Video validation complete: {len(valid)} valid concepts")
    else:
        pages = {int(p) for p in re.findall(r'Page\s+(\d+)', document_text)}
        for c in concepts:
            page = c.get('source_page_number')
            if not pages:
                # No markers found; keep concept even if page missing.
                valid.append(c)
                continue

            if page is None:
                c['source_page_number'] = min(pages)
                valid.append(c)
                continue

            try:
                page_int = int(page)
            except Exception:
                c['source_page_number'] = min(pages)
                valid.append(c)
                continue

            if page_int in pages:
                c['source_page_number'] = page_int
                valid.append(c)
            else:
                c['source_page_number'] = min(pages)
                valid.append(c)
    return valid

# --- Standardize ---
def _standardize_concept_format(concepts: List[Dict[str, Any]], is_video: bool) -> List[Dict[str, Any]]:
    standardized = []
    for c in concepts:
        std = {
            'concept_title': _sanitize_text_value(c.get('concept_title', c.get('concept', 'Untitled Concept'))),
            'concept_explanation': _sanitize_text_value(c.get('concept_explanation', c.get('explanation', '')))
        }
        if is_video:
            std['source_video_timestamp_start_seconds'] = c.get('source_video_timestamp_start_seconds', 0)
            std['source_video_timestamp_end_seconds'] = c.get('source_video_timestamp_end_seconds', 0)
            std['source_video_timestamp'] = c.get('source_video_timestamp', '')
        else:
            std['source_page_number'] = c.get('source_page_number', c.get('source_page', None))
        standardized.append(std)
    return standardized

def _extract_concepts_from_text(document_text: str, is_video: bool, source_field: str) -> List[Dict[str, Any]]:
    """
    Last resort: extract concepts directly from document text using heuristics.
    Splits text into sentences and groups them into concept-like chunks.
    """
    concepts = []
    sentences = re.split(r'[.!?]+', document_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    
    # Group every 2-3 sentences into a concept
    for i in range(0, min(len(sentences), 15), 3):
        group = sentences[i:i+3]
        if not group:
            continue
        
        # Use first sentence as title (truncated)
        title = group[0][:80].strip()
        # Remove common sentence starters
        title = re.sub(r'^(The|A|An|In|For|With|This|That)\s+', '', title, flags=re.IGNORECASE)
        
        # Use all sentences as explanation
        explanation = ' '.join(group)[:400]
        
        if len(title) >= 5:
            concepts.append({
                "concept_title": title,
                "concept_explanation": explanation,
                source_field: None
            })
        
        if len(concepts) >= 5:
            break
    
    logger.info(f"Extracted {len(concepts)} concepts from raw text")
    return concepts

def _extract_supplementary_concepts(document_text: str, is_video: bool, needed: int) -> List[Dict[str, Any]]:
    """
    Generate supplementary concepts when extraction yields too few results.
    Uses simpler prompt focused on main themes.
    """
    if needed <= 0:
        return []
    
    source_field = "source_video_timestamp" if is_video else "source_page_number"
    content_type = "video" if is_video else "document"
    
    prompt = f"""
Identify {needed} main themes or topics from this {content_type}.

Output as JSON array with format:
[{{"concept_title": "Theme Name", "concept_explanation": "Brief explanation"}}]

Content (first 2000 chars):
{document_text[:2000]}

JSON:
""".strip()
    
    try:
        raw = gradient_chat(prompt, max_tokens=500)
        raw = re.sub(r"```(?:json)?\n?", "", raw).strip("` \n")
        concepts = json.loads(raw)
        
        if isinstance(concepts, list):
            for c in concepts[:needed]:
                c[source_field] = None
            logger.info(f"Generated {len(concepts[:needed])} supplementary concepts")
            return concepts[:needed]
    except Exception as e:
        logger.warning(f"Supplementary concept generation failed: {e}")
    
    # Absolute fallback
    return _extract_concepts_from_text(document_text, is_video, source_field)[:needed]

# --- HTTP-based Embeddings API ---
def get_text_embedding(text: str) -> List[float]:
    """Generate embedding using HTTP API."""
    if not text or not text.strip():
        logger.warning("Empty text provided for embedding")
        return []
    
    if not DO_EMBEDDINGS_URL or not EMBEDDING_API_KEY:
        logger.error("DO_EMBEDDINGS_URL or EMBEDDING_API_KEY not configured")
        raise ValueError("Embedding API not configured")
    
    try:
        url = f"{DO_EMBEDDINGS_URL.rstrip('/')}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {EMBEDDING_API_KEY}",
        }
        data = {
            "model": MODEL_EMBEDDING_ID,
            "input": text,
        }
        
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        
        # Extract embedding from response
        embedding = body.get("data", [{}])[0].get("embedding")
        if not embedding:
            raise ValueError("No embedding in API response")
        
        # Validate dimension
        if len(embedding) == 0:
            raise ValueError("API returned empty embedding")
        
        return embedding
    except Exception as e:
        logger.error(f"HTTP embedding generation failed: {e}", exc_info=True)
        raise ValueError(f"Embedding generation failed: {e}")


def get_text_embeddings_in_batches(inputs: List[str], batch_size: int = 32) -> List[List[float]]:
    """Generate embeddings in batches using HTTP API."""
    if not inputs:
        return []
    
    if not DO_EMBEDDINGS_URL or not EMBEDDING_API_KEY:
        logger.error("DO_EMBEDDINGS_URL or EMBEDDING_API_KEY not configured")
        raise ValueError("Embedding API not configured")
    
    try:
        all_embeddings = []
        
        # Process in batches to avoid overwhelming the API
        for i in range(0, len(inputs), batch_size):
            batch = inputs[i:i+batch_size]
            
            url = f"{DO_EMBEDDINGS_URL.rstrip('/')}/embeddings"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {EMBEDDING_API_KEY}",
            }
            data = {
                "model": MODEL_EMBEDDING_ID,
                "input": batch,
            }
            
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            body = resp.json()
            
            # Extract embeddings from response
            batch_embeddings = [item.get("embedding") for item in body.get("data", [])]
            if not batch_embeddings or len(batch_embeddings) != len(batch):
                raise ValueError(f"Expected {len(batch)} embeddings, got {len(batch_embeddings)}")
            
            all_embeddings.extend(batch_embeddings)
            
            # Small delay between batches to avoid rate limiting
            if i + batch_size < len(inputs):
                time.sleep(0.1)
        
        # Validate all embeddings
        if len(all_embeddings) != len(inputs):
            raise ValueError(f"Embedding count mismatch: expected {len(inputs)}, got {len(all_embeddings)}")
        
        expected_dim = len(all_embeddings[0]) if all_embeddings else 0
        for i, emb in enumerate(all_embeddings):
            if not emb or len(emb) == 0:
                raise ValueError(f"Empty embedding at index {i}")
            if len(emb) != expected_dim:
                logger.error(f"Invalid embedding dimension at index {i}: {len(emb)} != {expected_dim}")
                raise ValueError(f"Embedding dimension mismatch at index {i}")
        
        logger.debug(f"Generated {len(all_embeddings)} embeddings with dimension {expected_dim}")
        return all_embeddings
        
    except Exception as e:
        logger.error(f"Batch embedding generation failed: {e}", exc_info=True)
        raise ValueError(f"Batch embedding failed: {e}")

def generate_mcq_from_key_concepts(key_concepts: List[Dict[str, Any]], comprehension_level: str = "Beginner") -> List[Dict[str, Any]]:
    """
    Generate multiple choice questions from key concepts with improved distractors.

    Args:
        key_concepts: List of key concept dictionaries
        comprehension_level: Target comprehension level (e.g., Beginner, Intermediate)

    Returns:
        List of MCQ dictionaries with question, options, and answer
    """
    if not key_concepts:
        logger.warning("No key concepts provided for MCQ generation")
        return []

    try:
        mcqs = []
        for concept in key_concepts:
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))

            if not concept_title or not concept_explanation:
                logger.warning(f"Skipping concept with missing title or explanation: {concept}")
                continue

            question = f"What is {concept_title}?"
            correct_answer = concept_explanation.split(".", 1)[0].strip()
            if len(correct_answer) < 10 and len(concept_explanation.split(".")) > 1:
                correct_answer = ". ".join(concept_explanation.split(".", 2)[:2]).strip()

            # Generate distractors using LLM service
            distractors = _generate_smart_distractors(
                concept=concept,
                all_concepts=key_concepts,
                correct_answer=correct_answer,
                comprehension_level=comprehension_level
            )

            # Ensure 4 options (1 correct + 3 distractors)
            if len(distractors) < 3:
                needed = 3 - len(distractors)
                try:
                    supplement_prompt = (
                        f"Provide {needed} additional plausible but incorrect distractors for a multiple-choice question about '{concept_title}'. "
                        f"Correct answer: '{correct_answer}'. "
                        f"Context: {concept_explanation[:400]} "
                        f"Each distractor must be 10-30 words, distinct, and believable. One per line."
                    )
                    resp_text = gradient_chat(supplement_prompt, max_tokens=200)
                    extra = [d.strip("- ").strip() for d in resp_text.split("\n") if d.strip()]
                    distractors.extend(extra[:needed])
                except Exception as e:
                    logger.warning(f"Failed to supplement distractors via LLM in MCQ generation: {e}")

            options = [correct_answer] + distractors[:3]
            mcqs.append({
                'question': question,
                'options': options,
                'answer': correct_answer
            })

        logger.info(f"Generated {len(mcqs)} MCQs from {len(key_concepts)} key concepts")
        return mcqs
    except Exception as e:
        logger.error(f"Error generating MCQs: {e}", exc_info=True)
        return []


def _generate_smart_distractors(concept: Dict[str, Any], all_concepts: List[Dict[str, Any]], correct_answer: str, comprehension_level: str) -> List[str]:
    """
    Generate smart distractors using LLM service and cross-embedding reranking.

    Args:
        concept: The key concept dictionary
        all_concepts: List of all key concepts
        correct_answer: The correct answer text
        comprehension_level: Target comprehension level

    Returns:
        List of 3 plausible distractors
    """
    try:
        distractors = []
        context = concept.get('concept_explanation', '')
        concept_title = concept.get('concept_title', '')

        # If HTTP LLM is unavailable, fall back to embedding-based distractors below

        # Iterative LLM prompt for distractor generation (2 passes)
        difficulty = "simple and clear" if comprehension_level == "Beginner" else "nuanced and challenging"
        prompt = (
            f"Generate 5 plausible but incorrect distractors for a multiple-choice question about '{concept_title}'. "
            f"The correct answer is: '{correct_answer}'. "
            f"The distractors should be {difficulty}, related to the topic, and plausible enough to challenge the learner. "
            f"Context: {context[:500]}. Each distractor should be a short sentence (10-30 words). "
            f"Do not include the correct answer or close paraphrases."
        )

        # First pass: Generate initial distractors using active model
        max_retries = 3
        delay = 2
        for attempt in range(1, max_retries + 1):
            try:
                resp_text = gradient_chat(prompt, max_tokens=200)
                initial_distractors = [d.strip("- ").strip() for d in resp_text.split("\n") if d.strip()]
                logger.debug(f"Generated {len(initial_distractors)} initial distractors on attempt {attempt}")
                break
            except Exception as e:
                logger.warning(f"LLM request failed on attempt {attempt}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    logger.error("All attempts failed for initial distractor generation")
                    return _fallback_distractors(concept, all_concepts, correct_answer, comprehension_level)

        # Second pass: Refine distractors for plausibility
        refine_prompt = (
            f"Refine these distractors for a question about '{concept_title}' to make them more plausible but still incorrect: "
            f"{'; '.join(initial_distractors)}. "
            f"Ensure they are {difficulty}, distinct from the correct answer: '{correct_answer}', and 10-30 words each."
        )

        for attempt in range(1, max_retries + 1):
            try:
                resp_text = gradient_chat(refine_prompt, max_tokens=200)
                llm_distractors = [d.strip("- ").strip() for d in resp_text.split("\n") if d.strip()]
                logger.debug(f"Generated {len(llm_distractors)} refined distractors on attempt {attempt}")
                break
            except Exception as e:
                logger.warning(f"LLM refine request failed on attempt {attempt}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                else:
                    logger.error("All attempts failed for distractor refinement")
                    return _fallback_distractors(concept, all_concepts, correct_answer, comprehension_level)

        # Filter and rank distractors using embeddings
        correct_embedding = get_text_embedding(correct_answer)
        for distractor in llm_distractors[:5]:
            if not distractor or len(distractor.split()) < 5:
                continue
            distractor_embedding = get_text_embedding(distractor)
            if not distractor_embedding or not correct_embedding:
                continue
            similarity = np.dot(correct_embedding, distractor_embedding) / (
                np.linalg.norm(correct_embedding) * np.linalg.norm(distractor_embedding)
            )
            if 0.5 <= similarity <= 0.8:
                distractors.append(distractor)

        # Supplement missing distractors using LLM (no hardcoded generics)
        if len(distractors) < 3:
            needed = 3 - len(distractors)
            try:
                supplement_prompt = (
                    f"Provide {needed} additional plausible but incorrect distractors for a question about '{concept_title}'. "
                    f"Correct answer: '{correct_answer}'. "
                    f"Each 10-30 words, distinct and believable. One per line."
                )
                resp_text = gradient_chat(supplement_prompt, max_tokens=200)
                extra = [d.strip("- ").strip() for d in resp_text.split("\n") if d.strip()]
                distractors.extend(extra[:needed])
            except Exception as e:
                logger.warning(f"Failed to supplement distractors via LLM: {e}")

        return distractors[:3]
    except Exception as e:
        logger.warning(f"Error generating smart distractors: {e}")
        return _fallback_distractors(concept, all_concepts, correct_answer, comprehension_level)


def _fallback_distractors(concept: Dict[str, Any], all_concepts: List[Dict[str, Any]], correct_answer: str, comprehension_level: str) -> List[str]:
    """Fallback distractor generation using embeddings; attempts LLM before returning fewer options."""
    distractors = []
    try:
        correct_embedding = get_text_embedding(correct_answer)
        other_concepts = [c for c in all_concepts if c != concept]
        if other_concepts:
            other_explanations = [c.get('concept_explanation', '') for c in other_concepts]
            other_embeddings = get_text_embeddings_in_batches(other_explanations)
            similarities = []
            for i, emb in enumerate(other_embeddings):
                if emb and correct_embedding:
                    sim = np.dot(correct_embedding, emb) / (np.linalg.norm(correct_embedding) * np.linalg.norm(emb))
                    if 0.5 <= sim <= 0.8:
                        similarities.append((i, sim, other_explanations[i]))

            similarities.sort(key=lambda x: x[1], reverse=True)
            distractors = [exp for _, _, exp in similarities[:3 - len(distractors)]]

        if len(distractors) < 3:
            needed = 3 - len(distractors)
            try:
                concept_title = concept.get('concept_title', '')
                prompt = (
                    f"Generate {needed} plausible but incorrect distractors for '{concept_title}'. "
                    f"Correct answer: '{correct_answer}'. One per line, 10-30 words."
                )
                resp_text = gradient_chat(prompt, max_tokens=120)
                extra = [d.strip("- ").strip() for d in resp_text.split("\n") if d.strip()]
                distractors.extend(extra[:needed])
            except Exception as e:
                logger.warning(f"LLM fallback distractor generation failed: {e}")

        return distractors[:3]
    except Exception as e:
        logger.warning(f"Error in fallback distractor generation: {e}")
        return []