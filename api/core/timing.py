"""Per-stage timing instrumentation for the ingest and query pipelines.

Emits one structured JSON line per stage under the `syntextai.timing` logger, so
durations can be grepped, shipped, or aggregated without adding an infrastructure
dependency. Deliberately log-based rather than PostHog: this is operational data
that must survive when an external analytics call fails, and it needs to work
identically in the worker, which has no request context.

    from api.core.timing import stage

    with stage("extract", file_id=file_id, file_type="pdf"):
        ...

Anything that fails inside the block still records a timing line, tagged with the
exception type, so slow failures are visible rather than silently missing. The
context manager never suppresses the exception.

Grep a log stream for the durations with:

    docker logs syntextai-worker-local 2>&1 | grep TIMING
"""
import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger("syntextai.timing")


def emit(event: str, ms: Optional[float] = None, **fields: Any) -> None:
    """Emit a single structured timing event."""
    payload: Dict[str, Any] = {"event": event}
    if ms is not None:
        payload["ms"] = round(ms)
    payload.update({k: v for k, v in fields.items() if v is not None})
    try:
        logger.info("TIMING %s", json.dumps(payload, default=str))
    except Exception:  # never let instrumentation break the pipeline
        logger.debug("Failed to emit timing event %s", event, exc_info=True)


@contextmanager
def stage(name: str, **fields: Any):
    """Time a pipeline stage and emit the duration on exit, success or failure.

    Yields a dict; anything put into it is merged into the emitted event, which
    lets a stage report counts it only discovers while running:

        with stage("extract", file_id=1) as ctx:
            ctx["pages"] = len(pages)
    """
    extra: Dict[str, Any] = {}
    start = time.perf_counter()
    error: Optional[str] = None
    try:
        yield extra
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        emit(name, ms=elapsed_ms, error=error, **{**fields, **extra})
