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
VERIFIER_EFFORT = _effort("VERIFIER_REASONING_EFFORT")
