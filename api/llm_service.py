import logging
import re
import json
import numpy as np
from typing import List, Dict, Any
import dspy
import requests
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
MODEL_ACCESS_KEY = os.getenv("MODEL_ACCESS_KEY")
INFERENCE_BASE_URL = os.getenv("INFERENCE_BASE_URL", "https://inference.do-ai.run/v1")
DO_EMBEDDINGS_URL = os.getenv("DO_EMBEDDINGS_URL", "https://api.digitalocean.com/v2/ai/embeddings")


logger = logging.getLogger(__name__)

# Active models (overridable via env)
CHAT_MODEL = os.getenv("MODEL_CHAT_ID", "openai-gpt-oss-20b")
EMBEDDING_MODEL = os.getenv("MODEL_EMBEDDING_ID", "multi-qa-mpnet-base-dot-v1")  # 768-d multilingual


def gradient_chat(prompt: str, max_tokens: int = 800) -> str:
    """Generate text using OpenAI-compatible chat completions over HTTP."""
    if not MODEL_ACCESS_KEY:
        logger.error("MODEL_ACCESS_KEY not configured for chat")
        return ""
    try:
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
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return text.strip()
    except Exception as e:
        logger.error(f"HTTP chat completion error: {e}")
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
    Extract key concepts from a document or video transcript in multiple passes.
    Uses iterative prompts on the full document text for better context.
    """
    if not document_text.strip():
        return []

    # LLM availability handled inside _extract_key_concepts_from_chunk via gradient_chat

    all_concepts = []
    logger.info(f"Processing full document ({len(document_text)} chars) for key concepts")

    # Process full text with iterative prompts
    concepts = _extract_key_concepts_from_chunk(document_text, language, comprehension_level, is_video, "Full Document")
    all_concepts.extend(concepts)

    logger.info(f"After extraction: {len(all_concepts)} concepts")

    all_concepts = _deduplicate_concepts(all_concepts)
    logger.info(f"After deduplication: {len(all_concepts)} concepts")

    all_concepts = _validate_references(all_concepts, document_text, is_video)
    logger.info(f"After validation: {len(all_concepts)} concepts")

    all_concepts = _standardize_concept_format(all_concepts, is_video)
    logger.info(f"After standardization: {len(all_concepts)} concepts")

    return all_concepts

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

        # --- Prompt template ---
        prompt_template = f"""
            You are an expert educator analyzing a {content_label}.
            Extract 5–10 key concepts from the following {content_label}.
            Write in {language} for a {comprehension_level} reader.

            CRITICAL INSTRUCTIONS:
            - Generate descriptive titles like "Machine Learning Basics" or "Data Structures"
            - DO NOT use markdown formatting (**bold**, *italic*, `code`)
            - DO NOT use just numbers like "01", "02" or timestamps like "00:30"
            - DO NOT include any special characters or formatting in titles
            - Focus on the main ideas and themes from the content
            - Each concept must have a clear, educational title

            For each concept, provide:
            - "concept_title": A clean descriptive title (e.g., "Artificial Intelligence", "Web Development")
            - "concept_explanation": 2–3 sentences explaining the concept clearly
            - "{source_field}": {source_label} where it appears
            - "source_video_timestamp_start_seconds": start time in seconds as number
            - "source_video_timestamp_end_seconds": end time in seconds as number

            Output ONLY valid JSON, no markdown, no commentary, no extra text.
            Example format:
            {example_json}

            Now analyze the content and return clean JSON only.
            Content:
            {{chunk}}
        """.strip()

        # --- Retry with exponential backoff ---
        delay = 2
        for attempt in range(1, max_retries + 1):
            logger.debug(f"Attempt {attempt}/{max_retries} extracting concepts from chunk {chunk_info}")

            prompt = prompt_template.replace("{chunk}", document_chunk[:6000])
            raw_json = ""

            try:
                raw_json = gradient_chat(prompt, max_tokens=800)
                logger.debug(f"Gradient raw response: {raw_json[:500]}...")

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
            title = concept.get("concept_title", "").strip()
            explanation = concept.get("concept_explanation", "").strip()
            if (
                title and explanation and
                len(title) >= 3 and
                not title.isdigit() and
                not title.startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')) and
                not re.match(r'^\d{2}:\d{2}', title)
            ):
                valid_concepts.append(concept)
            else:
                logger.warning(f"Skipping invalid concept: title='{title}', explanation='{explanation[:50]}...'")

        # --- Fallbacks if parsing fails ---
        if not valid_concepts and raw_json:
            logger.info("Attempting fallback to extract concepts from raw response")
            bullets = re.findall(r"[-•]\s*(.+)", raw_json)
            if bullets:
                valid_concepts = [
                    {
                        "concept_title": bp.strip().split(":")[0][:100].strip(),
                        "concept_explanation": ":".join(bp.split(":")[1:]).strip() or bp.strip(),
                        source_field: None
                    }
                    for bp in bullets
                    if len(bp.strip().split(":")[0]) >= 3 and not bp.strip().split(":")[0].isdigit()
                ]

        if not valid_concepts and len(document_chunk.strip()) > 100:
            logger.warning("No structured key concepts found — using fallback summary.")
            valid_concepts = [{
                "concept_title": "Summary Concept",
                "concept_explanation": document_chunk[:500],
                source_field: "00:00 - 00:00" if is_video else None
            }]

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
        # For videos, don't strictly validate timestamps since YouTube transcripts don't have explicit markers
        # Instead, just validate that concepts have basic timestamp info or assign defaults
        logger.debug(f"Validating {len(concepts)} video concepts")
        for c in concepts:
            # If concept already has timestamp info, keep it
            if c.get('source_video_timestamp_start_seconds') is not None:
                logger.debug(f"Concept '{c.get('concept_title')}' has existing timestamp")
                valid.append(c)
            else:
                # Assign default timestamp if missing (concept appears somewhere in video)
                logger.debug(f"Concept '{c.get('concept_title')}' missing timestamp, assigning default")
                c['source_video_timestamp_start_seconds'] = 0
                c['source_video_timestamp_end_seconds'] = 0
                c['source_video_timestamp'] = '00:00 - 00:00'
                valid.append(c)
        logger.debug(f"Video validation complete: {len(valid)} valid concepts")
    else:
        pages = {int(p) for p in re.findall(r'Page\s+(\d+)', document_text)}
        for c in concepts:
            page = c.get('source_page_number')
            if page is not None and (not pages or int(page) in pages):
                valid.append(c)
    return valid

# --- Standardize ---
def _standardize_concept_format(concepts: List[Dict[str, Any]], is_video: bool) -> List[Dict[str, Any]]:
    standardized = []
    for c in concepts:
        std = {
            'concept_title': c.get('concept_title', c.get('concept', 'Untitled Concept')),
            'concept_explanation': c.get('concept_explanation', c.get('explanation', ''))
        }
        if is_video:
            std['source_video_timestamp_start_seconds'] = c.get('source_video_timestamp_start_seconds', 0)
            std['source_video_timestamp_end_seconds'] = c.get('source_video_timestamp_end_seconds', 0)
            std['source_video_timestamp'] = c.get('source_video_timestamp', '')
        else:
            std['source_page_number'] = c.get('source_page_number', c.get('source_page', None))
        standardized.append(std)
    return standardized

# --- Embeddings ---
def get_text_embedding(text: str) -> List[float]:
    """Get embedding via HTTP. Try INFERENCE_BASE_URL first, then DigitalOcean endpoint. No local fallback."""
    if not MODEL_ACCESS_KEY:
        logger.error("MODEL_ACCESS_KEY not configured for embeddings")
        return []
    # Try OpenAI-compatible inference endpoint
    try:
        url = f"{INFERENCE_BASE_URL.rstrip('/')}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
        }
        data = {"model": EMBEDDING_MODEL, "input": text}
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        vec = (body.get("data", [{}])[0].get("embedding") or [])
        if vec:
            return vec
    except Exception as e:
        logger.warning(f"Primary embeddings endpoint failed: {e}")
    # Try DigitalOcean embeddings endpoint (uses same MODEL_ACCESS_KEY)
    try:
        headers = {
            "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
            "Content-Type": "application/json",
        }
        data = {"model": EMBEDDING_MODEL, "input": text}
        resp = requests.post(DO_EMBEDDINGS_URL, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        vec = (body.get("data", [{}])[0].get("embedding") or [])
        return vec
    except Exception as e:
        logger.error(f"DigitalOcean embeddings failed: {e}")
        return []


def get_text_embeddings_in_batches(inputs: List[str], batch_size: int = 50) -> List[List[float]]:
    if not inputs:
        return []
    if not MODEL_ACCESS_KEY:
        logger.error("MODEL_ACCESS_KEY not configured for embeddings")
        return [[] for _ in inputs]
    all_embeddings = []
    max_retries = 3
    base_delay = 1
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        retries = 0
        while retries < max_retries:
            try:
                # Primary: inference endpoint
                url = f"{INFERENCE_BASE_URL.rstrip('/')}/embeddings"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                }
                data = {"model": EMBEDDING_MODEL, "input": batch}
                resp = requests.post(url, headers=headers, json=data, timeout=120)
                resp.raise_for_status()
                body = resp.json()
                vectors = [item.get("embedding", []) for item in body.get("data", [])]
                if len(vectors) < len(batch):
                    vectors.extend([[] for _ in range(len(batch) - len(vectors))])
                all_embeddings.extend(vectors)
                break
            except Exception as e1:
                logger.warning(f"Primary embeddings failed for batch {i}: {e1}")
                # Secondary: DigitalOcean embeddings
                try:
                    headers = {
                        "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                        "Content-Type": "application/json",
                    }
                    data = {"model": EMBEDDING_MODEL, "input": batch}
                    resp = requests.post(DO_EMBEDDINGS_URL, headers=headers, json=data, timeout=120)
                    resp.raise_for_status()
                    body = resp.json()
                    vectors = [item.get("embedding", []) for item in body.get("data", [])]
                    if len(vectors) < len(batch):
                        vectors.extend([[] for _ in range(len(batch) - len(vectors))])
                    all_embeddings.extend(vectors)
                    break
                except Exception as e2:
                    retries += 1
                    if retries < max_retries:
                        delay = base_delay * (2 ** retries)
                        logger.warning(f"Retrying embeddings batch {i} in {delay:.1f}s: {e2}")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Embeddings permanently failed for batch {i}: {e2}")
                        all_embeddings.extend([[] for _ in batch])
                        break
        time.sleep(1)
    return all_embeddings

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
                generic_distractors = [
                    f"A concept not related to {concept_title}.",
                    f"A common misunderstanding of {concept_title}.",
                    f"An incorrect definition of {concept_title}."
                ]
                distractors.extend(generic_distractors[:3 - len(distractors)])

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