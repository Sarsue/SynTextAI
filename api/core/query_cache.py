"""Answering the same question twice, for a few minutes.

WHY

A question costs an embedding call, a retrieval, and one or more model calls,
and takes several seconds. Teams ask the same things: the morning after a policy
lands, several people ask what it says; somebody asks, closes the tab, and asks
again; two staff ask the same thing an hour apart. Recomputing an identical
answer from an unchanged set of documents buys nothing.

WHAT IS IN THE KEY, AND WHY EACH PART

Everything that can change the answer, or this returns somebody else's.

- **workspace**, because that is the set of documents the answer comes from,
  and it is also the tenant boundary. Retrieval scopes purely by workspace when
  it is given, so every member of a workspace would get the same answer anyway,
  which is what makes sharing one between them safe.
- **the documents version**, so a newly ingested or deleted file invalidates
  immediately instead of leaving five minutes of answers that predate it.
  "I uploaded it and it says it cannot find it" is worse than a slow answer.
- **file_id**, because a question asked against one document is a different
  question.
- **language and comprehension level**, because both change the wording.
- **the question**, normalised only for case and whitespace. Nothing cleverer:
  two questions that differ by a word are two questions.
- **the conversation so far**, hashed. "What about the second one?" means
  nothing without it, and a cache that ignored history would answer a follow-up
  with the reply to somebody else's.

WHAT THE NORMALISATION ACTUALLY BUYS TODAY: NOTHING. MEASURED.

The route saves the question before queueing the run, so the conversation the
worker formats already contains that question, verbatim, spacing and capitals
and all. It reaches the key through the history fingerprint whatever the
normalisation does to the copy beside it. Driving the app confirmed it: the
same question asked twice hits in 0.33s against 20.4s cold, and the same
question retyped with different capitals misses entirely.

That is the correct behaviour, just a lower hit rate than the normalisation
implies, so it is written down rather than left to be rediscovered. The
normalisation is kept because it costs three lines and becomes load-bearing the
moment the current turn stops being part of the history that is hashed, which
is the obvious way to raise the hit rate later. Doing that now would mean
deciding what "the same conversation" means when the last turn differs, and
that is a bigger question than this cache should answer.

ONLY WITH A WORKSPACE

When no workspace is given, the document set is "everything this person can
reach", which varies per person and has no single version to invalidate
against. That path is not cached rather than cached carefully.

WHAT IS NOT STORED

The retrieved chunks. They carry the full text of every page retrieved, tens of
kilobytes per answer, and nothing a customer sees needs them on a repeat. The
count is kept so timing stays honest.

FAILURE

Every function here swallows its own errors. A cache that is down means slower
answers, which is what the product did before this existed, and must never mean
no answer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .events import _get_client, is_enabled

logger = logging.getLogger(__name__)

# Short on purpose. This exists to absorb the same question being asked twice
# in the same sitting, not to serve yesterday's answer.
TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL", "300"))

_KEY_PREFIX = "syntext:answer:"
_VERSION_PREFIX = "syntext:docsver:"

_WHITESPACE = re.compile(r"\s+")


def _normalise(question: str) -> str:
    return _WHITESPACE.sub(" ", (question or "").strip().lower())


async def document_version(workspace_id: int) -> int:
    """How many times this workspace's documents have changed.

    Missing means zero, which is correct after a Redis restart: the cached
    answers were in the same Redis and are gone too, so there is nothing stale
    for an old version number to protect against.
    """
    client = await _get_client()
    if client is None:
        return 0
    try:
        raw = await client.get(f"{_VERSION_PREFIX}{int(workspace_id)}")
        return int(raw or 0)
    except Exception as e:
        logger.warning("Could not read the documents version: %s", e)
        return 0


async def bump_document_version(workspace_id: Optional[int]) -> None:
    """Call when a workspace's documents change: ingested, deleted, moved.

    Every cached answer for the workspace becomes unreachable at once, because
    the version is part of the key. The old entries are not deleted; they expire
    on their own TTL, which is cheaper than finding them and no less correct.
    """
    if workspace_id is None:
        return
    client = await _get_client()
    if client is None:
        return
    try:
        await client.incr(f"{_VERSION_PREFIX}{int(workspace_id)}")
    except Exception as e:
        logger.warning("Could not bump the documents version: %s", e)


def _history_fingerprint(formatted_history: Any) -> str:
    """A stable hash of the conversation so far, whatever shape it arrives in.

    `format_user_chat_history` returns a list of {role, content} dictionaries,
    not a string, and the first version of this called .encode() on it. Every
    read and every write raised, was caught, and logged a warning, so the cache
    silently never worked. The tests passed a string and saw none of it; the
    live app found it in one question.

    json.dumps with sorted keys rather than str(), so two equal histories cannot
    hash differently because a dictionary was built in another order.
    """
    if not formatted_history:
        return ""
    if isinstance(formatted_history, str):
        material = formatted_history
    else:
        try:
            material = json.dumps(formatted_history, sort_keys=True, default=str)
        except Exception:
            material = repr(formatted_history)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def _key(
    *,
    workspace_id: int,
    question: str,
    formatted_history: Any,
    language: str,
    comprehension_level: str,
    file_id: Optional[int],
) -> str:
    version = await document_version(workspace_id)
    material = "␟".join([
        str(int(workspace_id)),
        str(version),
        str(file_id if file_id is not None else ""),
        (language or "").lower(),
        (comprehension_level or "").lower(),
        _normalise(question),
        _history_fingerprint(formatted_history),
    ])
    return _KEY_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


async def get(
    *,
    workspace_id: Optional[int],
    question: str,
    formatted_history: Any,
    language: str,
    comprehension_level: str,
    file_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """A previous answer to this exact question, or None."""
    if workspace_id is None or not is_enabled():
        return None
    client = await _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(await _key(
            workspace_id=workspace_id, question=question,
            formatted_history=formatted_history, language=language,
            comprehension_level=comprehension_level, file_id=file_id,
        ))
        if not raw:
            return None
        result = json.loads(raw)
        # Marked so a run record shows why it has no retrieval trace, rather
        # than looking like retrieval returned nothing.
        result["cached"] = True
        return result
    except Exception as e:
        logger.warning("Could not read a cached answer: %s", e)
        return None


async def put(
    *,
    workspace_id: Optional[int],
    question: str,
    formatted_history: Any,
    language: str,
    comprehension_level: str,
    file_id: Optional[int] = None,
    result: Dict[str, Any],
) -> None:
    """Keep this answer for TTL_SECONDS.

    Refusals are cached too, and deliberately. "I could not find enough
    evidence" is as expensive to produce as an answer, and a document arriving
    is exactly what bumps the version and drops it.

    An error is never cached: those are usually a service being briefly down,
    and holding one for five minutes turns a blip into an outage.
    """
    if workspace_id is None or not is_enabled():
        return
    if not result or not result.get("response") or result.get("error"):
        return
    client = await _get_client()
    if client is None:
        return

    chunks = result.get("context_chunks") or []
    keepable = {k: v for k, v in result.items() if k != "context_chunks"}
    keepable["context_chunk_count"] = len(chunks)

    try:
        await client.set(
            await _key(
                workspace_id=workspace_id, question=question,
                formatted_history=formatted_history, language=language,
                comprehension_level=comprehension_level, file_id=file_id,
            ),
            json.dumps(keepable, default=str),
            ex=TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("Could not cache an answer: %s", e)
