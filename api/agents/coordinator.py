"""The coordinator: which documents does this question need?

It runs after one broad search across the whole workspace, so it decides from
evidence rather than from the question alone. The search says which documents
hold anything relevant; the coordinator says which of them the answer actually
needs, and what to look for in each.

WHY A MODEL DECIDES THIS AND NOT A THRESHOLD

The obvious rule, "use every document with a passage in the top 25", sends a
single-document question to five workers whenever the workspace holds five
similar manuals, which is the normal case. The opposite rule, "only documents
that dominate the ranking", loses exactly the questions this exists for: on the
citation benchmark the second source of a multi-document question sits at rank
12 to 24, present but buried. Neither a count nor a score can tell those apart.
Reading the question next to each document's best passages can.

WHAT IT NEVER DOES

It never adds work to a question that one document answers, and it never
fails the question. No candidates, one candidate, a reply it cannot read, or a
choice of one document all mean the same thing: the single-document path,
which is the pipeline as it was before any of this existed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from api.agents.models import COORDINATOR_EFFORT, COORDINATOR_MODEL
from api.services import llm_service

logger = logging.getLogger(__name__)

# Most workers one question may start. Each is a search and an answer call, run
# in parallel, so the cost of a worker is money more than time.
MAX_DOCUMENTS = 4

# How many documents the coordinator is shown, and how much of each. Enough to
# recognise what a document is; not so much that choosing becomes reading.
SHOWN_DOCUMENTS = 6
PASSAGES_PER_DOCUMENT = 2
PASSAGE_CHARS = 400


@dataclass
class Assignment:
    file_id: int
    file_name: str
    focus: str


@dataclass
class Plan:
    assignments: List[Assignment] = field(default_factory=list)
    reason: str = "single"  # single | multi | scoped | one_candidate | unreadable | error

    @property
    def is_multi(self) -> bool:
        return len(self.assignments) >= 2


def candidates(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Documents that the broad search found anything in, strongest first.

    Strength is the summed rank-fusion score of a document's passages, the
    same measure the evidence set already ranks passages by.
    """
    docs: Dict[int, Dict[str, Any]] = {}
    for e in evidence:
        fid = e.get("file_id")
        if fid is None:
            continue
        d = docs.setdefault(fid, {"file_id": fid, "file_name": e.get("file_name") or "document",
                                  "score": 0.0, "passages": []})
        d["score"] += float(e.get("similarity_score") or e.get("hybrid_score") or 0.0)
        d["passages"].append(e)
    return sorted(docs.values(), key=lambda d: d["score"], reverse=True)


def _prompt(question: str, docs: List[Dict[str, Any]]) -> str:
    blocks = []
    for n, d in enumerate(docs, start=1):
        shown = "\n".join(
            "  > " + re.sub(r"\s+", " ", (p.get("content") or ""))[:PASSAGE_CHARS]
            for p in d["passages"][:PASSAGES_PER_DOCUMENT]
        )
        blocks.append(f"Document {n}: {d['file_name']}\n{shown}")
    return (
        "You decide which documents are needed to answer a question. A search has "
        "already found the documents below, with a sample of what each contains.\n\n"
        "Choose the documents whose content is NEEDED for a complete answer:\n"
        "- If one document answers the whole question, choose only that one.\n"
        "- Choose several only when the question asks for things that live in "
        "different documents, or asks to compare or combine them, or when several "
        "documents each give their own answer to the same question and the reader "
        "should see each one.\n"
        f"- Never choose more than {MAX_DOCUMENTS}.\n\n"
        "Reply with one line per chosen document and nothing else, in the form\n"
        "<document number>: <what to find in that document, in a few words>\n\n"
        f"QUESTION\n{question}\n\nDOCUMENTS\n\n" + "\n\n".join(blocks) + "\n"
    )


_LINE_RE = re.compile(r"(?m)^\W*(?:document\s*)?(\d+)\s*[:.)\-]\s*(.*)$", re.I)


async def plan(question: str, evidence: List[Dict[str, Any]], *, scoped: bool = False) -> Plan:
    if scoped:
        return Plan(reason="scoped")
    docs = candidates(evidence)[:SHOWN_DOCUMENTS]
    if len(docs) < 2:
        return Plan(reason="one_candidate")
    try:
        reply = await llm_service.gradient_chat(
            _prompt(question, docs), max_tokens=600,
            reasoning_effort=COORDINATOR_EFFORT, model=COORDINATOR_MODEL,
        )
        chosen: List[Assignment] = []
        for n, focus in _LINE_RE.findall(reply or ""):
            i = int(n)
            if 1 <= i <= len(docs) and all(a.file_id != docs[i - 1]["file_id"] for a in chosen):
                d = docs[i - 1]
                chosen.append(Assignment(d["file_id"], d["file_name"], focus.strip() or question))
        chosen = chosen[:MAX_DOCUMENTS]
        if not chosen:
            logger.info({"event": "coordinator.unreadable"})
            return Plan(reason="unreadable")
        result = Plan(chosen, "multi" if len(chosen) >= 2 else "single")
        logger.info({"event": "coordinator.plan", "reason": result.reason,
                     "documents": len(chosen), "candidates": len(docs)})
        return result
    except Exception as e:
        logger.warning({"event": "coordinator.failed", "error": str(e)[:300]})
        return Plan(reason="error")
