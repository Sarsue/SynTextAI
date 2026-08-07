"""Keep customer content out of the logs.

WHY THIS EXISTS

A question is customer content. For the businesses this product is sold to, it
is usually the most sensitive content they have: "what is Maria Okafor's
treatment plan" is PHI, "what did we settle the Ade matter for" is privileged,
"what did we pay Chen last year" is payroll. Three log lines carried the raw
question, two of them at INFO, which meant every question a customer ever asked
was sitting in application logs that get shipped to a hosting provider, kept for
weeks, and read by whoever can see the log stream.

Nothing about that is exotic. It is the ordinary way a document product leaks:
not through the documents, which everyone is careful with, but through the
questions, which look like debugging output.

WHAT TO LOG INSTEAD

A fingerprint and a length. The fingerprint is stable for the same text, so two
log lines about one question can still be tied together and a repeated question
is still recognisable, without the words being recoverable from the digest.

    q7f3a91c2 len=54

When the text itself is genuinely needed, which is nearly always a local
debugging session rather than production, set LOG_QUERY_TEXT=true. It is off by
default because a default is what runs in production, and this is the kind of
setting nobody remembers to turn back off.
"""
from __future__ import annotations

import hashlib
import os

# Off by default, deliberately. Turning it on in a local shell is one command;
# leaving it on in production is a data incident nobody notices for months.
LOG_QUERY_TEXT = os.getenv("LOG_QUERY_TEXT", "false").strip().lower() == "true"


def safe_text(value: str | None, label: str = "q") -> str:
    """A loggable stand-in for customer text.

    Short enough to sit inside a structured log line, stable enough to correlate
    two lines about the same question, and one-way, so the digest in a log
    archive is not the question.
    """
    text = (value or "").strip()
    if not text:
        return f"{label}:empty"
    if LOG_QUERY_TEXT:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{label}{digest} len={len(text)}"
