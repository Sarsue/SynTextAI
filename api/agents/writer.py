"""The writer: one answer from several documents' answers.

It reads what the workers wrote, not what they read. Each worker's answer is a
few sentences with citation markers; the passages behind them stay out of this
prompt, which is what keeps it small however many documents were consulted.

THE MARKERS ARE THE CONTRACT

Every worker numbered its passages from 1. Before the writer sees anything,
each worker's markers are shifted into one shared numbering, so [Segment 3]
from the second document becomes, say, [Segment 15], and the combined answer's
markers resolve against one list. The writer is told to carry markers across
unchanged. If it drops every one of them, its prose is not used: the workers'
answers go out under their document names instead, which is plainer and fully
cited, rather than a smoother answer nobody can check.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from api.agents.document_worker import WorkerResult
from api.agents.models import WRITER_EFFORT, WRITER_MODEL
from api.services import llm_service
from api.services.syntext_agent import _CITATION_RE, _cited_segments, _declined, Draft, SyntextAgent

logger = logging.getLogger(__name__)


def _shift(text: str, offset: int) -> str:
    if not offset:
        return text

    def sub(m: re.Match) -> str:
        nums = ", ".join(str(int(n.strip()) + offset) for n in m.group(1).split(","))
        return f"[Segment {nums}]"

    return _CITATION_RE.sub(sub, text)


def merge(results: List[WorkerResult]) -> Tuple[List[Dict[str, Any]], List[Tuple[WorkerResult, str]]]:
    """One segment list for everything the workers cited, and each worker's
    answer renumbered against it."""
    segments: List[Dict[str, Any]] = []
    shifted: List[Tuple[WorkerResult, str]] = []
    for r in results:
        shifted.append((r, _shift(r.draft.text, len(segments))))
        segments.extend(r.draft.segments)
    return segments, shifted


def _plain(shifted: List[Tuple[WorkerResult, str]]) -> str:
    return "\n\n".join(f"**{r.file_name}**\n\n{text}" for r, text in shifted)


def _prompt(question: str, shifted: List[Tuple[WorkerResult, str]]) -> str:
    parts = "\n\n".join(
        f"ANSWER FROM {r.file_name}\n{text}" for r, text in shifted
    )
    return (
        "Several documents were each asked the same question separately. Write ONE "
        "answer to the question from their answers below.\n\n"
        "Rules:\n"
        "1. Use only what the answers below say. Add nothing.\n"
        "2. Every fact keeps the [Segment N] marker that follows it in the answers "
        "below, copied exactly. Never invent, renumber or drop a marker.\n"
        "3. If different documents give different answers to the same thing, do "
        "not choose between them and do not merge them. Give each with the "
        "document it comes from, then ask which one the reader means.\n"
        "4. Keep any caution that a value was read from a figure.\n"
        "5. Be complete and direct. No preamble about documents or answers.\n\n"
        f"QUESTION\n{question}\n\n{parts}\n\nANSWER\n"
    )


async def write(question: str, results: List[WorkerResult]) -> Draft:
    answered = [r for r in results if r.draft.kind == "answer"]
    if not answered:
        # Nobody could cite. An uncited answer is still better than nothing;
        # otherwise the refusal.
        uncited = [r for r in results if r.draft.kind == "uncited"]
        return uncited[0].draft if uncited else Draft.final(results[0].draft.text if results else "")

    segments, shifted = merge(answered)
    _, targets = SyntextAgent()._format_context_and_sources(segments)

    if len(answered) == 1:
        # One document had the answer. Nothing to combine.
        return Draft("answer", shifted[0][1], segments, targets)

    text = ""
    try:
        text = await llm_service.gradient_chat(
            _prompt(question, shifted), max_tokens=1500,
            reasoning_effort=WRITER_EFFORT, model=WRITER_MODEL,
        ) or ""
    except Exception as e:
        logger.warning({"event": "writer.failed", "error": str(e)[:300]})

    valid = [n for n in _cited_segments(text) if 1 <= n <= len(segments)]
    if not text.strip() or _declined(text) or not valid:
        logger.info({"event": "writer.fallback_to_plain", "had_text": bool(text.strip())})
        text = _plain(shifted)
    logger.info({"event": "writer.done", "documents": len(answered), "segments": len(segments)})
    return Draft("answer", text, segments, targets)
