"""What a reader may see of how an answer was made.

The run record already holds the whole story: which path the question took,
which documents the coordinator sent it to, which of them answered, and what
the verifier found. None of it reached the reader, so an answer checked claim
by claim looked exactly like one that was not.

This is the reader's version of that record. It says what happened in terms a
person can check: documents by name, claims by count, time in seconds. It
deliberately leaves out what is ours rather than theirs: model names, cost,
retrieval scores, internal ids.

One function, so the answer that arrives live over the websocket and the same
answer after a reload are described identically.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def public_trace(
    record: Optional[Mapping[str, Any]],
    seconds: Optional[float],
    names: Mapping[int, str],
) -> Optional[Dict[str, Any]]:
    """`record` is a stored run record (workers/worker.py _run_record);
    `names` maps file ids to the names this reader may see."""
    if not record:
        return None

    def name(fid: Any) -> str:
        return names.get(fid) or "a document"

    workers = record.get("workers") or []
    multi = bool(workers)
    if multi:
        documents = [
            {"name": name(w.get("file_id")), "answered": w.get("kind") in ("answer", "uncited")}
            for w in workers
        ]
    else:
        documents = [
            {"name": name(fid), "answered": True}
            for fid in (record.get("cited_file_ids") or [])
        ]

    v = record.get("verification") or {}
    checked = None
    if v.get("status") == "ok" and (v.get("claims") or 0) > 0:
        checked = {
            "claims": int(v.get("claims") or 0),
            "confirmed": int(v.get("supported") or 0),
            "moved": int(v.get("recited") or 0),
            "unconfirmed": int(v.get("unverified") or 0),
        }

    return {
        "path": "multi" if multi else "single",
        "documents": documents,
        "checked": checked,
        "seconds": round(seconds, 1) if seconds is not None else None,
    }
