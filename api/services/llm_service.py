"""The inference client.

ASYNC, AND WHY IT MATTERS MORE THAN IT LOOKS

Every function here is awaited, and every caller up the chain is async, because
the worker runs queries concurrently on one event loop.

This module used to use `requests` and `time.sleep` while every one of its
callers was an `async def`. The worker declares
`asyncio.Semaphore(QUERY_CONCURRENCY)` and starts a task per query, so it
believes it is running four at a time; a blocking POST with a 120-second
timeout stops the loop dead, so it ran one, and while it ran nothing else
happened either. Not the websocket manager, not the heartbeat the healthcheck
reads, not the queue poll. The retry backoff slept the loop for another one,
two and four seconds.

That cost grows with the agent loop, which makes several model calls per
answer rather than one.

One shared AsyncClient, so connections are pooled and TLS is negotiated once
rather than per call. Close it on shutdown with `aclose_client()`.
"""
import asyncio
import logging
from typing import Callable, List, Dict, Any, Optional
import httpx
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


# One client for the process. Created lazily rather than at import, because a
# client bound to the wrong event loop is worse than no pooling at all, and at
# import time there may not be a loop yet.
_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

# Generous, because inference is slow and an agent loop makes several calls per
# answer. The pool is what stops those calls renegotiating TLS every time.
_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    limits=_LIMITS,
                    timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
                )
    return _client


async def aclose_client() -> None:
    """Call from the app's shutdown hook so sockets are not left dangling."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _post_json(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    attempts: int = 3,
    accept: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """POST with exponential backoff, yielding the loop between attempts.

    The backoff used to be time.sleep, which stops every other query on this
    worker as well as the one being retried.

    `accept` decides whether a 200 is actually a useful answer. It exists
    because this endpoint returns HTTP 200 with an empty content field often
    enough to matter: the reasoning model sometimes spends its whole budget
    thinking. Without it, retrying only on transport errors made a flaky call
    look like a deterministic failure, and the chunk contextualiser lost 46% of
    its work to responses that a second attempt would have satisfied.
    """
    client = await get_client()
    delay = 1.0
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
            if accept is None or accept(body):
                return body
            last_err = ValueError("response rejected by caller's accept()")
        except Exception as e:
            last_err = e
        await asyncio.sleep(delay)
        delay *= 2
    logger.error(f"Inference request to {url} failed after {attempts} attempts: {last_err}")
    return None


def _has_content(body: Dict[str, Any]) -> bool:
    """True when a chat response carries usable text, not just a shell."""
    choices = body.get("choices") or []
    if not choices:
        return False
    first = choices[0]
    content = (first.get("message") or {}).get("content") or first.get("text")
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return bool(content and str(content).strip())


async def chat_with_tools(
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

    body = await _post_json(url, headers, data)
    if not body:
        return {}
    choices = body.get("choices") or []
    if not choices:
        logger.warning("Tool chat returned no choices")
        return {}
    # A turn that only asks for tools has no content, and that is a valid turn.
    # Emptiness alone is not a failure here.
    return choices[0].get("message") or {}


# The configured chat model reasons before it answers, and that reasoning is
# spent from the same budget as the reply. Ask for too few tokens and the whole
# allowance goes on thinking, leaving content empty: measured 2026-07-30, 120
# returned nothing however tersely the prompt was written, 400 was reliable.
#
# The floor lives here rather than in each caller because a caller that has not
# read this comment picks a number that looks generous for one sentence and
# gets silence. That is exactly what happened to the chunk contextualiser,
# which asked for 200, and had 308 of 316 chunks come back empty.
MIN_COMPLETION_TOKENS = 500


async def gradient_chat(prompt: str, max_tokens: int = 800) -> str:
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
        "max_tokens": max(int(max_tokens), MIN_COMPLETION_TOKENS),
    }

    body = await _post_json(url, headers, data, accept=_has_content)
    if not body:
        return ""

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
        return ""
    return str(content).strip()


def token_count(content: str, model: str = None) -> int:
    return max(1, int(len(content.split()) * 1.5))

async def generate_explanation(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_tokens: int = None) -> str:
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
        response = await gradient_chat(truncated_chunk, max_tokens=1500)
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
def _embedding_request(payload_input: Any) -> tuple[str, Dict[str, str], Dict[str, Any]]:
    if not DO_EMBEDDINGS_URL or not EMBEDDING_API_KEY:
        logger.error("DO_EMBEDDINGS_URL or EMBEDDING_API_KEY not configured")
        raise ValueError("Embedding API not configured")
    return (
        f"{DO_EMBEDDINGS_URL.rstrip('/')}/embeddings",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {EMBEDDING_API_KEY}",
        },
        {"model": MODEL_EMBEDDING_ID, "input": payload_input},
    )


async def get_text_embedding(text: str) -> List[float]:
    """Generate embedding using HTTP API."""
    if not text or not text.strip():
        logger.warning("Empty text provided for embedding")
        return []

    url, headers, data = _embedding_request(text)
    body = await _post_json(url, headers, data)
    if not body:
        raise ValueError("Embedding generation failed")

    embedding = (body.get("data") or [{}])[0].get("embedding")
    if not embedding:
        raise ValueError("No embedding in API response")
    return embedding


# Batches in flight at once during ingestion. Bounded rather than unbounded:
# a 1000-page PDF is 20 batches, and firing all of them at the embedding
# endpoint at once is how a large upload takes the endpoint down for everybody
# else on the worker.
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "4"))


async def get_text_embeddings_in_batches(
    inputs: List[str], batch_size: int = 32
) -> List[List[float]]:
    """Generate embeddings in batches, several batches at a time.

    Batches used to run one after another with a 0.1s sleep between them, so a
    1000-page document paid that latency serially. They now run
    EMBED_CONCURRENCY at a time and results are reassembled in input order,
    which callers rely on: the nth embedding must belong to the nth chunk, or
    every citation in that document points at the wrong page.
    """
    if not inputs:
        return []

    batches = [inputs[i:i + batch_size] for i in range(0, len(inputs), batch_size)]
    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def run(batch: List[str]) -> List[List[float]]:
        async with semaphore:
            url, headers, data = _embedding_request(batch)
            body = await _post_json(url, headers, data)
            if not body:
                raise ValueError("Batch embedding failed")
            got = [item.get("embedding") for item in body.get("data", [])]
            if len(got) != len(batch):
                raise ValueError(f"Expected {len(batch)} embeddings, got {len(got)}")
            return got

    # gather preserves ordering of its arguments regardless of completion order.
    results = await asyncio.gather(*(run(b) for b in batches))
    all_embeddings = [emb for batch_result in results for emb in batch_result]

    if len(all_embeddings) != len(inputs):
        raise ValueError(
            f"Embedding count mismatch: expected {len(inputs)}, got {len(all_embeddings)}"
        )

    expected_dim = len(all_embeddings[0]) if all_embeddings else 0
    for i, emb in enumerate(all_embeddings):
        if not emb:
            raise ValueError(f"Empty embedding at index {i}")
        if len(emb) != expected_dim:
            raise ValueError(
                f"Embedding dimension mismatch at index {i}: {len(emb)} != {expected_dim}"
            )

    logger.debug(f"Generated {len(all_embeddings)} embeddings with dimension {expected_dim}")
    return all_embeddings





