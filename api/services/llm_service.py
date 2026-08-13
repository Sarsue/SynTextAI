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
import json
import logging
from typing import Callable, List, Dict, Any, Optional
import base64
import httpx
import os
import time
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

# The model that reads a rendered page, chosen by measurement on 2026-08-12
# against page 9 of the Carrier manual, a two-dimensional charging chart whose
# every cell had already been verified by hand.
#
#   llama-4-maverick          146s/page   0 wrong in 48 cells
#   nemotron-nano-12b-v2-vl    59s/page   3 wrong in 48 cells
#   gemma-4-31B-it            timed out
#
# The fast one invents digits: it returned 82/28/78 for a row that reads
# 82/28/80. In a service manual a wrong charge or torque value is a safety
# claim, not a typo, so accuracy wins and the cost is paid in routing rather
# than in a cheaper model. Anything swapped in here must be measured the same
# way before it ships.
VISION_MODEL = os.getenv("MODEL_VISION_ID", "llama-4-maverick")

# Vision can be pointed at a different provider from chat, and needs to be.
#
# Measured 2026-08-13: this endpoint writes at ~3 tokens/second. The published
# median for llama-4-maverick across providers is 123 t/s, and Azure serves the
# same weights at 368. We are not running a slow model, we are running an
# ordinary model roughly forty times slower than everybody else, and the pricing
# is the same either way (~$0.11-0.14 per 69-page manual whichever provider).
#
# The consequence is not only speed. The same prompt on the same page returned
# byte-identical output in 188s and in 113s, a 66% spread, which is wider than
# any prompt or DPI difference worth measuring. Nothing about this call can be
# measured here.
#
# Chat is a different model with different behaviour and is left alone, so
# moving vision is two environment variables and no code.
VISION_BASE_URL = os.getenv("VISION_BASE_URL") or INFERENCE_BASE_URL
VISION_API_KEY = os.getenv("VISION_API_KEY") or MODEL_ACCESS_KEY

# 150 worked; 110 made the small model burn its budget on reasoning and return
# nothing. Higher costs image tokens for no measured gain on these pages.
VISION_DPI = int(os.getenv("VISION_DPI", "150"))

# A token budget is a TIME budget at this endpoint, which is the thing the
# original 8,000 here missed.
#
# Measured 2026-08-13: this endpoint writes at roughly 3 tokens/second, so 8,000
# tokens is not "headroom for a dense page", it is a 44-minute page. And that is
# not hypothetical: a prompt A/B run was killed after 33 minutes on a single
# page whose sibling prompt finished the same page in 148 seconds. The model had
# started rambling and the budget let it.
#
# The densest page measured in the HVAC corpus produced about 700 tokens. 2,500
# is more than three times the worst real page and caps a runaway at about 14
# minutes, with VISION_DEADLINE below as the actual backstop.
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "2500"))

# Gap between chunks, not total request time. Since the response is streamed,
# httpx resets this on every chunk that arrives, so it only catches a stalled
# connection. It cannot bound a slow-but-alive stream, which is what
# VISION_DEADLINE is for.
VISION_TIMEOUT = float(os.getenv("VISION_TIMEOUT", "900"))

# Total wall-clock budget for one page, enforced by hand in the streaming loop.
# A page takes 120-180s when it is behaving; 600 leaves generous room for a slow
# one and still stops a run away.
VISION_DEADLINE = float(os.getenv("VISION_DEADLINE", "600"))

# How many pages are read at once.
#
# Eight was tried first and was worse than useless: the endpoint queues these
# server side, so every request slowed until all eight passed the timeout and
# the whole document fell back to the text layer. Concurrency here does not buy
# throughput past a small number, it converts one slow page into eight failed
# ones.
#
# Three, with a timeout long enough that a queued request still lands. The real
# ceiling is the endpoint, not this process.
VISION_CONCURRENCY = int(os.getenv("VISION_CONCURRENCY", "3"))

VISION_PROMPT = (
    "Transcribe this page as markdown. Reproduce every table as a real markdown "
    "table with all rows and columns in their original order. Do not summarise, "
    "do not omit rows, and do not correct anything you think is wrong. "
    "For a diagram, describe what it shows and transcribe every label, keeping "
    "each label with the part it points to."
)

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


# Sampling temperature. Never set until 2026-08-05, so every call in the
# product ran at the endpoint's default, which samples.
#
# That is the right default for writing prose and the wrong one for this. The
# benchmark showed it: four runs of identical code scored between 12 and 18 of
# 22 on citations, with fifteen of twenty-two questions changing verdict
# between runs. A customer asking the same question twice was getting
# materially different pages back, and no change smaller than six citations
# could be measured at all.
#
# It compounds in the agent loop, where every turn is a fresh sampling
# decision about which tool to call and with what query, so the paths diverge
# early and never reconverge.
#
# Not zero. Zero is greedy decoding, which on a reasoning model can lock it
# into a degenerate loop, and this endpoint gives no seed to make runs
# genuinely reproducible anyway. Low enough to be near-deterministic in
# practice, and overridable for anything that ever wants variety.
TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.1"))


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


async def read_page(image_png: bytes, hint: str = "") -> str:
    """Read one rendered page and return it as markdown.

    WHY THIS EXISTS

    `page.get_text()` returns characters in PDF storage order, so a table
    arrives as a column of loose values with every row destroyed. Measured on
    a 433-page corpus of HVAC service manuals: prose questions scored 4/4 on
    citations, table questions 5/12, figure questions 0/4. One question cited
    the correct page and still answered 78 where the page reads 74, because
    the row that value belongs to no longer existed by the time retrieval or
    the model saw it.

    A vision model reads the page as a page. The same chart came back with
    every row intact.

    NEVER RAISES

    An empty string means "use the text layer", and every caller must treat it
    that way. A page that fails here should cost accuracy, never the document.

    `hint` is the text layer for this page, passed to the model as a
    transcription aid rather than as truth: the characters are all correct, it
    is only their order that is wrong.
    """
    if not VISION_API_KEY:
        logger.error("No API key configured for vision (VISION_API_KEY or MODEL_ACCESS_KEY)")
        return ""

    content: List[Dict[str, Any]] = [{"type": "text", "text": VISION_PROMPT}]
    if hint:
        content.append({
            "type": "text",
            "text": (
                "The text layer of this page is below. Every character in it is "
                "correct and its order is not. Use it to resolve anything "
                "ambiguous in the image, never to decide the layout:\n\n"
                + hint[:4000]
            ),
        })
    content.append({
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image_png).decode()
        },
    })

    data = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": VISION_MAX_TOKENS,
        # Zero, because this is transcription. Any creativity here is a
        # hallucinated digit in somebody's refrigerant charge.
        "temperature": 0,
        # STREAMED, AND NOT FOR THE REASON STREAMING IS USUALLY ADDED.
        #
        # Nobody watches this output arrive; it goes into a database. It is
        # streamed because a page takes two to three minutes and a request that
        # sends nothing for three minutes is an idle connection. Idle
        # connections get closed by whatever sits between here and the model,
        # and that is what the 408s and RemoteProtocolErrors were: measured
        # 2026-08-13, a second image in one request died with HTTP 408 after
        # exactly 601 seconds, which is a proxy timeout, not a model failing.
        # A stream is never idle.
        #
        # It is also the only way to see where the time goes. Measured the same
        # day: the first token arrives in 1.5 to 3 seconds and the rest write
        # out at roughly 3 tokens/second. That single number ruled out image
        # size, routing and queueing as explanations for a slow page, all three
        # of which had been guessed at and two of which had been wrong.
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {VISION_API_KEY}",
    }
    url = f"{VISION_BASE_URL.rstrip('/')}/chat/completions"

    try:
        parts: List[str] = []
        finish_reason = None
        started = time.monotonic()
        first_token_at = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(VISION_TIMEOUT, read=VISION_TIMEOUT)) as client:
            async with client.stream("POST", url, headers=headers, json=data) as response:
                if response.status_code != 200:
                    # The body has not been read yet on a streamed response, and
                    # httpx refuses to look at it until it has been.
                    await response.aread()
                    logger.warning("Vision read refused: %s", response.status_code)
                    return ""
                async for line in response.aiter_lines():
                    if time.monotonic() - started > VISION_DEADLINE:
                        # Discarded, not truncated. A cut-off table looks exactly
                        # like a complete one: the rows that never arrived leave
                        # no trace, and the number guardrail only catches numbers
                        # that were invented, never rows that are missing. The
                        # text layer is the honest fallback.
                        logger.warning(
                            "Vision read exceeded %ss and was discarded; the page "
                            "keeps its text layer", VISION_DEADLINE,
                        )
                        return ""
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        # A malformed keepalive or comment frame is not worth
                        # losing a two-minute page over.
                        continue
                    choice = (event.get("choices") or [{}])[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    if piece:
                        if first_token_at is None:
                            first_token_at = time.monotonic() - started
                        parts.append(piece)

        text = "".join(parts)
        if not text:
            # Seen in practice: a reasoning model spends the whole budget
            # thinking and returns no content at all.
            logger.warning(
                "Vision read returned nothing (finish_reason=%s). Falling back "
                "to the text layer.", finish_reason
            )
        else:
            logger.debug(
                "Vision page read in %.0fs (first token %.1fs, %d chars)",
                time.monotonic() - started, first_token_at or 0, len(text),
            )
        return text.strip()
    except Exception as e:
        logger.warning("Vision read failed: %s", type(e).__name__)
        return ""


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
        "temperature": TEMPERATURE,
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





