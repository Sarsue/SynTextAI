"""The reader's account of how an answer was made.

Built from the run record and nothing else, so what it can say is exactly
what the pipeline recorded. It must never carry cost, model names or ids.
"""
from api.agents.trace import public_trace


def test_a_multi_document_answer_names_each_document_and_whether_it_answered():
    record = {
        "workers": [{"file_id": 1, "kind": "answer"}, {"file_id": 2, "kind": "final"}],
        "verification": {"status": "ok", "claims": 5, "supported": 3, "recited": 1, "unverified": 1},
        "cost": {"cost_usd": 0.07, "by_model": {"anthropic/claude-sonnet-5": {}}},
        "cited_file_ids": [1, 2],
    }
    t = public_trace(record, 9.43, {1: "osha.pdf", 2: "irs_583.pdf"})
    assert t == {
        "path": "multi",
        "documents": [{"name": "osha.pdf", "answered": True},
                      {"name": "irs_583.pdf", "answered": False}],
        "checked": {"claims": 5, "confirmed": 3, "moved": 1, "unconfirmed": 1},
        "seconds": 9.4,
    }
    assert "cost" not in str(t) and "sonnet" not in str(t)


def test_a_document_the_reader_cannot_see_is_not_named():
    t = public_trace({"cited_file_ids": [7]}, None, {})
    assert t["documents"] == [{"name": "a document", "answered": True}]


def test_nothing_checked_says_nothing_about_checking():
    t = public_trace({"cited_file_ids": [1], "verification": {"status": "skipped", "claims": 0}}, 2.0, {1: "a.pdf"})
    assert t["path"] == "single" and t["checked"] is None


def test_no_run_no_trace():
    assert public_trace(None, 1.0, {}) is None


def test_the_live_trace_accepts_a_timezone_aware_start_and_never_raises():
    """Postgres hands started_at back timezone-aware. The first version
    subtracted it from a naive utcnow() and failed the whole answer."""
    from datetime import datetime, timedelta, timezone
    from api.workers.worker import _live_trace

    result = {"context_chunks": [{"file_id": 1, "file_name": "a.pdf"}],
              "verification": {"status": "ok", "claims": 2, "supported": 2}}
    aware = datetime.now(timezone.utc) - timedelta(seconds=3)
    t = _live_trace(result, aware)
    assert t["documents"] == [{"name": "a.pdf", "answered": True}]
    assert 2 < t["seconds"] < 30

    naive = datetime.utcnow() - timedelta(seconds=3)
    assert _live_trace(result, naive)["seconds"] > 2
    # Garbage in is a missing line, not a failed answer.
    assert _live_trace({"context_chunks": "nonsense"}, "not a date") is None
