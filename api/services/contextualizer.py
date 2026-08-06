"""Give every chunk enough context to be found on its own.

THE PROBLEM

A chunk is embedded and indexed in isolation, so it carries only the words on
that page. Page 8 of the ADA guide says "if doing so is readily achievable"
while explaining parking spaces. Page 19 states the 15-employee threshold under
a heading about information sources. Nothing in either page's own text says
which document it is from or what section it belongs to, so a question phrased
the way a shop owner would phrase it has to match the page's incidental
vocabulary or miss it.

THE METHOD, AND HOW OURS DIFFERS

Anthropic's Contextual Retrieval prepends 50-100 tokens of model-written
context to each chunk before embedding and before building the keyword index.
They report retrieval failures falling 35% with contextual embeddings, and 49%
with contextual BM25 as well, at $1.02 per million document tokens.

They send the entire document with every chunk and rely on prompt caching to
make that affordable. Our inference endpoint has no prompt caching, so sending
a 67,000-token publication once per chunk would cost more than the product
earns. Instead each document is summarised once, and that summary is what
travels with each chunk. It is the affordable approximation of the same idea:
the chunk is described in terms of the document it came from rather than in
isolation.

Written into its own column rather than mixed into the page text. The text a
customer reads when they follow a citation has to be what the document
actually says, not what a model said about it.

NEVER FAILS AN UPLOAD

Every failure path here returns "no context" rather than raising. A document
that ingests without context is searchable and slightly harder to find. A
document that fails to ingest is not there at all.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List

from api.services.llm_service import gradient_chat

logger = logging.getLogger(__name__)

# Chunks contextualised at once. Same reasoning as embedding concurrency: a
# large upload should not monopolise the inference endpoint.
CONTEXT_CONCURRENCY = int(os.getenv("CONTEXT_CONCURRENCY", "8"))

# Off by default until it has been measured on the benchmark. Ingestion is the
# one path where a mistake is expensive to undo: it is written into the index
# and only a re-upload or a backfill takes it out again.
CONTEXTUALIZE = os.getenv("CONTEXTUALIZE_CHUNKS", "false").lower() == "true"

# How much of the document is read to write the summary. Enough to see what the
# document is and how it is organised, not so much that the call is slow.
PRECIS_CHARS = 6000
# A chunk longer than this is trimmed for the context call only. The full text
# is still what gets stored and served.
CHUNK_CHARS_FOR_CONTEXT = 2000


async def _document_precis(units: List[Dict[str, Any]], file_name: str) -> str:
    """Two or three sentences on what this document is. One call per document."""
    head = ""
    for u in units:
        head += (u.get("text") or "") + "\n"
        if len(head) >= PRECIS_CHARS:
            break
    if not head.strip():
        return ""

    prompt = (
        "Below is the beginning of a document. In two or three sentences, say "
        "what the document is, who it is for, and what subjects it covers. "
        "Write only those sentences.\n\n"
        f"File name: {file_name}\n\n{head[:PRECIS_CHARS]}"
    )
    try:
        return (await gradient_chat(prompt, max_tokens=300) or "").strip()
    except Exception as e:
        logger.warning(f"Document precis failed for {file_name}: {e}")
        return ""


async def _chunk_context(precis: str, file_name: str, unit: Dict[str, Any]) -> str:
    text = (unit.get("text") or "")[:CHUNK_CHARS_FOR_CONTEXT]
    if not text.strip():
        return ""

    prompt = (
        "You are indexing one passage of a larger document so that it can be "
        "found by search.\n\n"
        f"The document is: {file_name}\n"
        f"What the document covers: {precis}\n\n"
        "Here is the passage:\n"
        f"{text}\n\n"
        "Write one short sentence, under 30 words, saying what this passage is "
        "about and where it sits in that document. Name the specific subject, "
        "rule, form or figure it deals with, using the document's own words. "
        "Write only that sentence, with no preamble."
    )
    try:
        out = (await gradient_chat(prompt, max_tokens=200) or "").strip()
        # A model that ignores the instruction and writes an essay would
        # otherwise dominate the chunk it is supposed to be describing.
        return out[:400]
    except Exception as e:
        logger.warning(f"Chunk context failed for {file_name}: {e}")
        return ""


async def add_context(units: List[Dict[str, Any]], file_name: str) -> int:
    """Add a 'context' key to each unit in place. Returns how many got one.

    Units without context keep the key absent, and every caller must treat that
    as ordinary rather than as an error.
    """
    if not CONTEXTUALIZE or not units:
        return 0

    precis = await _document_precis(units, file_name)
    if not precis:
        logger.info(f"No precis for {file_name}; skipping chunk context")
        return 0

    semaphore = asyncio.Semaphore(CONTEXT_CONCURRENCY)

    async def one(unit: Dict[str, Any]) -> None:
        async with semaphore:
            ctx = await _chunk_context(precis, file_name, unit)
            if ctx:
                unit["context"] = ctx

    await asyncio.gather(*(one(u) for u in units), return_exceptions=True)
    done = sum(1 for u in units if u.get("context"))
    logger.info(
        f"Contextualised {done}/{len(units)} chunks for {file_name}"
    )
    return done


def embedding_text(unit: Dict[str, Any]) -> str:
    """What actually gets embedded: the context, then the passage.

    Context first because it is the part that says which document and which
    section, and a truncating embedder should keep that rather than lose it.
    """
    ctx = (unit.get("context") or "").strip()
    text = (unit.get("text") or unit.get("content") or "")
    return f"{ctx}\n\n{text}" if ctx else text
