"""Which model each agent runs on.

One setting per role, so a stronger model can be tried on the verifier alone,
or the writer alone, and measured there, without moving every other call with
it. Each falls back to MODEL_CHAT_ID, so leaving them unset changes nothing.

Reasoning effort is per role for the same reason. It comes out of the same
token budget as the output on gpt-oss, and a role that emits one line per claim
needs far less of it than one that writes the answer.
"""
import os

from api.services.llm_service import CHAT_MODEL, CHAT_REASONING_EFFORT


def _model(env: str) -> str:
    return (os.getenv(env) or "").strip() or CHAT_MODEL


def _effort(env: str) -> str:
    return (os.getenv(env) or CHAT_REASONING_EFFORT).strip().lower()


COORDINATOR_MODEL = _model("COORDINATOR_MODEL")
COORDINATOR_EFFORT = _effort("COORDINATOR_REASONING_EFFORT")

WORKER_MODEL = _model("WORKER_MODEL")

WRITER_MODEL = _model("WRITER_MODEL")
WRITER_EFFORT = _effort("WRITER_REASONING_EFFORT")

VERIFIER_MODEL = _model("VERIFIER_MODEL")
# Low by default, not the chat setting. A verdict per claim needs little
# thinking, and thinking is where the time went: 17 claims at medium took
# 30s of a 78s answer (driven 2026-09-23). Known-answer check, gpt-oss-20b,
# two runs each: low kept 34/36 correct citations, caught 35/36 wrong pages
# and 14.5/15 wrong values; medium 33.5, 35 and 15. No measurable difference.
VERIFIER_EFFORT = (os.getenv("VERIFIER_REASONING_EFFORT") or "low").strip().lower()
