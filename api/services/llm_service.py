import logging
from typing import List, Dict, Any
import requests
import time
import os
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


def chat_with_tools(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] | None = None,
    max_tokens: int = 1500,
) -> Dict[str, Any]:
    """One turn of an OpenAI-compatible chat, with tool calling.

    Returns the assistant message as the server sent it, so the caller can see
    both `content` and `tool_calls` and decide whether the turn was an answer or
    a request to run something. Returns {} on failure, which the caller must
    treat as "no turn happened" rather than as an empty answer.

    Separate from gradient_chat because that one takes a single prompt string
    and returns a single string. A tool loop needs the whole message list, since
    every tool result has to be appended and sent back for the next turn.
    """
    if not MODEL_ACCESS_KEY:
        logger.error("MODEL_ACCESS_KEY not configured for chat")
        return {}

    url = f"{INFERENCE_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
    }
    data: Dict[str, Any] = {
        "model": CHAT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        data["tools"] = tools
        data["tool_choice"] = "auto"

    last_err: Exception | None = None
    delay = 1
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=120)
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if not choices:
                last_err = ValueError("no_choices")
            else:
                message = choices[0].get("message") or {}
                # A turn that only asks for tools has no content, and that is a
                # valid turn. Emptiness alone is not a failure here.
                if message:
                    return message
                last_err = ValueError("empty_message")
        except Exception as e:
            last_err = e

        time.sleep(delay)
        delay *= 2

    logger.error(f"Tool chat completion error after retries: {last_err}")
    return {}


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
def token_count(content: str, model: str = None) -> int:
    return max(1, int(len(content.split()) * 1.5))

def generate_explanation(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_tokens: int = None) -> str:
    """Generates an explanation/answer for a prompt via the real LLM (gradient_chat).

    Previously routed through a DSPy predictor that was never actually
    configured (the "configuration" step was a no-op `pass`), so this always
    fell through to a canned placeholder string with no [Segment N] citations
    in it. Since query_pipeline's citation check requires at least one valid
    citation whenever context was retrieved, that meant every real chat query
    was refused with "I couldn't find enough evidence..." regardless of what
    was actually in the documents. `language`/`comprehension_level` are kept
    as parameters for call-site compatibility; the main caller (query_pipeline)
    already bakes both directly into the prompt text.

    The budget is in tokens and is enforced in tokens. It used to be named
    max_context_length and applied as `text_chunk[:max_context_length]`, a
    character slice against a number every caller passed in tokens. Callers
    handing it MAX_TOKENS_CONTEXT got a window roughly four times smaller than
    they asked for, and every caller that passed nothing got the 2000-character
    default: a prompt cut off inside its own instructions, long before any
    document text. Defaults to MAX_TOKENS_CONTEXT so leaving it out is safe.
    """
    if not text_chunk:
        logging.warning("generate_explanation called with empty text_chunk.")
        return ""

    budget = MAX_TOKENS_CONTEXT if max_context_tokens is None else int(max_context_tokens)
    if token_count(text_chunk) > budget:
        # token_count approximates 1.5 tokens per whitespace word, so convert
        # back through the same ratio rather than inventing a second one.
        logging.warning(
            "Prompt of ~%d tokens exceeds the %d-token budget; truncating.",
            token_count(text_chunk), budget,
        )
        words = text_chunk.split()
        truncated_chunk = " ".join(words[: max(1, int(budget / 1.5))])
    else:
        truncated_chunk = text_chunk

    try:
        response = gradient_chat(truncated_chunk, max_tokens=1500)
        if response:
            return response
        logging.warning(f"LLM returned empty response for chunk: {truncated_chunk[:50]}...")
        return ""
    except Exception as e:
        logging.error(f"Error generating explanation: {e}", exc_info=True)
        return ""

# --- Key concept extraction ---






# --- Deduplication ---


# --- Reference validation ---

# --- Standardize ---



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





