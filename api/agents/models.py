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


# Not the chat model. The coordinator drives a search loop, and gpt-oss-20b
# drove it badly on the first real runs (2026-09-28): near-identical searches
# over and over, and most questions ended with no decision at all.
#
# Tried in its place, same code, same three questions, one run each:
#
#     researcher              one turn   car records   Ontario   tax + OSHA
#     Qwen3-235B-Instruct     9-11s      65s           20s       62s, 12/13 confirmed
#     gpt-oss-120b, low       1-2s       21s           10s       36s, 11/11 confirmed
#
# Qwen judged better (it answered car records in part, where 120b said the
# documents do not cover it) but on DeepInfra every turn took ten seconds and
# it ran out of time before deciding. Osas chose 120b for speed. About a tenth
# of a cent a question either way.
COORDINATOR_MODEL = (os.getenv("COORDINATOR_MODEL") or "").strip() or "openai/gpt-oss-120b"
# Low, as measured above. Each turn is a short tool call; thinking is time.
COORDINATOR_EFFORT = (os.getenv("COORDINATOR_REASONING_EFFORT") or "low").strip().lower()

# A document worker does two jobs: it searches its document (a tool loop, the
# same kind of job as the coordinator's) and then writes its answer. They were
# one setting; searching is now its own, on the coordinator's model, because
# gpt-oss-20b drove a search loop badly (see COORDINATOR_MODEL). Writing stays
# on WORKER_MODEL.
# Writing stays on gpt-oss-20b at medium. Checked 2026-09-28 against 120b at
# low, same passages from the same researcher, 12 benchmark questions, one run:
# 20b-medium cited an expected page 10/12, carried the expected facts 8/12,
# 25/27 claims confirmed, 6.3s; 120b-low 8/12, 6/12, 22/26, 5.2s, and refused
# twice with the answer in front of it.
WORKER_MODEL = _model("WORKER_MODEL")
WORKER_SEARCH_MODEL = (os.getenv("WORKER_SEARCH_MODEL") or "").strip() or COORDINATOR_MODEL
WORKER_SEARCH_EFFORT = (os.getenv("WORKER_SEARCH_REASONING_EFFORT") or COORDINATOR_EFFORT).strip().lower()

WRITER_MODEL = _model("WRITER_MODEL")
WRITER_EFFORT = _effort("WRITER_REASONING_EFFORT")

VERIFIER_MODEL = _model("VERIFIER_MODEL")
# Low by default, not the chat setting. A verdict per claim needs little
# thinking, and thinking is where the time went: 17 claims at medium took
# 30s of a 78s answer (driven 2026-09-23). Known-answer check, gpt-oss-20b,
# two runs each: low kept 34/36 correct citations, caught 35/36 wrong pages
# and 14.5/15 wrong values; medium 33.5, 35 and 15. No measurable difference.
VERIFIER_EFFORT = (os.getenv("VERIFIER_REASONING_EFFORT") or "low").strip().lower()
