"""The verifier: does the page an answer cites actually say what the answer says?

Until this existed, a citation was checked for its FORMAT only. compose makes
sure every [Segment N] refers to a segment that exists, and nothing asked
whether segment N says the thing the sentence in front of it claims. An answer
could put a figure from page 14 against a link to page 12 and reach the reader
looking exactly as trustworthy as a correct one.

HOW IT WORKS

1. The draft is split into claims: each sentence (or table row, or bullet) that
   carries a marker, together with any unmarked sentences just before it on the
   same line, since a marker at the end of a bullet usually covers the bullet.
2. One call checks every claim against only the passages it cites. One call,
   not one per claim: the passages are listed once and the claims refer to
   them by number, so a passage cited five times is paid for once.
3. A claim that fails gets a second chance. The other retrieved passages are
   ranked by the words and numbers they share with the claim, the best three go
   to a second call, and if one of them states the claim the citation moves
   there. The answer was right and the pointer was wrong, which is the common
   failure and the one a reader cannot detect.
4. A claim nothing supports keeps its text and loses its link, and the reader
   is told it could not be confirmed. Flagged rather than deleted, because a
   wrong verdict here would otherwise remove a true sentence without trace.

WHAT IT NEVER DOES

It never blocks the answer. Any failure (no reply, a reply it cannot parse, an
exception) leaves the draft exactly as compose wrote it and records why. A
claim the reply does not mention is left alone and counted as unchecked, not
treated as a failure.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from api.agents.models import VERIFIER_EFFORT, VERIFIER_MODEL
from api.services import llm_service
from api.services.answer_composer import UNVERIFIED_MARK, Draft, _CITATION_RE

logger = logging.getLogger(__name__)

# How many other passages a failed claim may be re-checked against. Three keeps
# the second call small; the ranking that picks them is crude, and past the
# first few its choices are noise.
RECITE_CANDIDATES = 3

# Passages are cut to this many characters in the verifier's prompt. A chunk is
# about 300 tokens, so this is nearly always the whole passage; the cap exists
# for the page-sized chunks of documents not yet re-ingested.
PASSAGE_CHARS = 2400

# A sentence ends at . ! or ? followed by whitespace or the end of the line,
# optionally with citation markers after the punctuation. "3.5 psi" does not
# end a sentence because the period is followed by a digit.
_SENTENCE_RE = re.compile(
    r"[^\n]*?(?:[.!?](?:\s*\[Segments?[^\]]*\])*(?=\s|$)|$)"
)
_WORD_RE = re.compile(r"[a-z0-9]+(?:[.,/-][0-9]+)*")
_STOP = {
    "the", "and", "for", "are", "with", "that", "this", "from", "not", "you",
    "your", "can", "will", "must", "should", "have", "has", "was", "were", "its",
    "any", "all", "may", "per", "use", "into", "than", "then", "when", "which",
    "segment", "segments",
}


@dataclass
class Claim:
    start: int
    end: int
    text: str
    segments: List[int]


@dataclass
class Report:
    status: str = "skipped"  # skipped | ok | error
    claims: int = 0
    supported: int = 0
    recited: int = 0
    unverified: int = 0
    unchecked: int = 0
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None}
        return out


def split_claims(text: str) -> List[Claim]:
    """Every cited statement in the answer, with where it sits in the text."""
    claims: List[Claim] = []
    offset = 0
    # Where the last line with words on it began. A line holding nothing but
    # markers ("[Segment 1] [Segment 2]" closing an answer) cites the text
    # above it; without this such answers produced no claims at all and went
    # out unchecked.
    prev_text_start: Optional[int] = None
    for line in text.split("\n"):
        pending_start: Optional[int] = None
        for m in _SENTENCE_RE.finditer(line):
            span = m.group(0)
            if not span.strip():
                continue
            s, e = offset + m.start(), offset + m.end()
            markers = [
                int(n.strip())
                for mk in _CITATION_RE.finditer(span)
                for n in mk.group(1).split(",")
            ]
            if not markers:
                # Carried into the next cited sentence on this line, if any.
                if pending_start is None:
                    pending_start = s
                continue
            start = pending_start if pending_start is not None else s
            pending_start = None
            body = text[start:e]
            claim_text = _CITATION_RE.sub("", body)
            claim_text = re.sub(r"\s+", " ", claim_text)
            claim_text = re.sub(r" ([.,;:!?])", r"\1", claim_text).strip(" -*|#>").strip()
            if not claim_text:
                if prev_text_start is None:
                    continue
                start = prev_text_start
                body = text[start:e]
                claim_text = re.sub(r"\s+", " ", _CITATION_RE.sub("", body))
                claim_text = re.sub(r" ([.,;:!?])", r"\1", claim_text).strip(" -*|#>").strip()
                if not claim_text:
                    continue
            seen: List[int] = []
            for n in markers:
                if n not in seen:
                    seen.append(n)
            claims.append(Claim(start, e, claim_text, seen))
        if _CITATION_RE.sub("", line).strip(" -*|#>\t"):
            prev_text_start = offset
        offset += len(line) + 1
    return claims


def _terms(text: str) -> set:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= 3 and w not in _STOP}


def _passage(n: int, seg: Dict[str, Any]) -> str:
    where = seg.get("file_name") or "document"
    if seg.get("page_number") is not None:
        where += f", page {seg['page_number']}"
    body = (seg.get("content") or "")[:PASSAGE_CHARS]
    return f"[Passage {n}, {where}]\n{body}"


_VERDICT_RE = re.compile(r"(?m)^\W*(?:claim\s*)?(\d+)\s*[:.)\-]\s*\**\s*(NOT\s+SUPPORTED|SUPPORTED)", re.I)
_PICK_RE = re.compile(r"(?m)^\W*(?:claim\s*)?(\d+)\s*[:.)\-]\s*\**\s*(?:passage\s*)?(NONE|\d+)", re.I)


def _check_prompt(claims: List[Tuple[int, Claim]], segments: List[Dict[str, Any]]) -> str:
    used = sorted({n for _, c in claims for n in c.segments if 1 <= n <= len(segments)})
    passages = "\n\n".join(_passage(n, segments[n - 1]) for n in used)
    lines = "\n".join(
        f"{i}. {c.text} (cites passage {', '.join(str(n) for n in c.segments)})"
        for i, c in claims
    )
    return (
        "You are checking whether each claim is stated by the passages it cites.\n\n"
        "For each claim, read ONLY the passages it cites.\n"
        "SUPPORTED means those passages state the claim, or it follows directly "
        "from them with the same figures and terms.\n"
        "NOT SUPPORTED means they do not say it, say something different, or are "
        "only about the same topic. A number, unit, date or name that differs is "
        "NOT SUPPORTED. Differences of wording alone are fine.\n\n"
        "Reply with exactly one line per claim and nothing else, in the form\n"
        "1: SUPPORTED\n2: NOT SUPPORTED\n\n"
        f"PASSAGES\n\n{passages}\n\nCLAIMS\n\n{lines}\n"
    )


def _recite_prompt(items: List[Tuple[int, Claim, List[int]]], segments: List[Dict[str, Any]]) -> str:
    used = sorted({n for _, _, cands in items for n in cands})
    passages = "\n\n".join(_passage(n, segments[n - 1]) for n in used)
    lines = "\n".join(
        f"{i}. {c.text} (candidates: {', '.join(str(n) for n in cands)})"
        for i, c, cands in items
    )
    return (
        "For each claim, decide which ONE of its candidate passages states it, "
        "with the same figures and terms. If none of them does, say NONE. Being "
        "about the same topic is not enough.\n\n"
        "Reply with exactly one line per claim and nothing else, in the form\n"
        "1: 7\n2: NONE\n\n"
        f"PASSAGES\n\n{passages}\n\nCLAIMS\n\n{lines}\n"
    )


def _candidates(claim: Claim, segments: List[Dict[str, Any]]) -> List[int]:
    want = _terms(claim.text)
    if not want:
        return []
    scored = []
    for n, seg in enumerate(segments, start=1):
        if n in claim.segments:
            continue
        overlap = len(want & _terms(seg.get("content") or ""))
        if overlap:
            scored.append((overlap, -n, n))
    scored.sort(reverse=True)
    return [n for _, _, n in scored[:RECITE_CANDIDATES]]


def _rewrite(text: str, claim: Claim, new_segment: Optional[int]) -> str:
    body = text[claim.start:claim.end]
    if new_segment is not None:
        # Every marker in the claim points at the passage that holds it. The
        # first becomes the new one and the rest go, so a claim that cited
        # 2 and 5, both wrong, does not end up citing 7 twice.
        first = True

        def sub(_m):
            nonlocal first
            if first:
                first = False
                return f"[Segment {new_segment}]"
            return ""

        body = _CITATION_RE.sub(sub, body)
    else:
        body = _CITATION_RE.sub("", body)
        body = re.sub(r"[ \t]+([.,;:!?])", r"\1", body).rstrip()
        body += UNVERIFIED_MARK
    body = re.sub(r"[ \t]{2,}", " ", body)
    return text[:claim.start] + body + text[claim.end:]


async def _ask(prompt: str) -> str:
    return await llm_service.gradient_chat(
        prompt, max_tokens=1200, reasoning_effort=VERIFIER_EFFORT, model=VERIFIER_MODEL
    )


async def verify(draft: Draft) -> Tuple[Draft, Report]:
    """Check every cited claim in the draft. Returns the draft to render and
    what happened, for the run record."""
    report = Report()
    if draft.kind != "answer" or not draft.segments:
        return draft, report

    claims = split_claims(draft.text)
    report.claims = len(claims)
    if not claims:
        return draft, report

    segments = draft.segments
    try:
        numbered = list(enumerate(claims, start=1))
        reply = await _ask(_check_prompt(numbered, segments))
        verdicts = {int(n): v.upper().startswith("SUPPORTED") for n, v in _VERDICT_RE.findall(reply or "")}
        if not verdicts:
            report.status = "error"
            report.error = "unparseable" if reply else "empty"
            report.unchecked = len(claims)
            logger.warning({"event": "verifier.no_verdicts", "reason": report.error})
            return draft, report

        failed: List[Tuple[int, Claim]] = []
        for i, c in numbered:
            if i not in verdicts:
                report.unchecked += 1
            elif verdicts[i]:
                report.supported += 1
            else:
                failed.append((i, c))

        moves: Dict[int, Optional[int]] = {i: None for i, _ in failed}
        to_recheck = [(i, c, _candidates(c, segments)) for i, c in failed]
        to_recheck = [t for t in to_recheck if t[2]]
        if to_recheck:
            reply = await _ask(_recite_prompt(to_recheck, segments))
            picks = {int(n): p for n, p in _PICK_RE.findall(reply or "")}
            for i, _, cands in to_recheck:
                pick = picks.get(i, "NONE")
                if pick.upper() != "NONE" and int(pick) in cands:
                    moves[i] = int(pick)

        # Rewrite from the end so earlier offsets stay valid.
        text = draft.text
        by_index = dict(numbered)
        for i in sorted(moves, key=lambda k: by_index[k].start, reverse=True):
            text = _rewrite(text, by_index[i], moves[i])
            if moves[i] is None:
                report.unverified += 1
            else:
                report.recited += 1

        report.status = "ok"
        logger.info({"event": "verifier.done", **report.as_dict()})
        return Draft(draft.kind, text, draft.segments, draft.source_targets), report
    except Exception as e:  # never blocks the answer
        logger.warning({"event": "verifier.failed", "error": str(e)[:300]})
        report.status = "error"
        report.error = type(e).__name__
        return draft, report
