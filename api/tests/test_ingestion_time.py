"""How long a document takes to become answerable.

WHERE THE NUMBER COMES FROM, AND WHY NOT WHERE IT WAS ASKED FOR

The request was the gap between the file row and its last chunk. `chunks`
carries no timestamp at all, so that needed a migration and could only ever have
answered for documents ingested after it.

`agent_runs` already holds it: every ingest is a row with run_type
'ingest_file', created_at when it was queued, started_at when a worker took it,
and finished_at when it was done. Nothing new is written, and it covers
documents ingested before anybody thought to ask.

WHAT IS ASSERTED

That it measures work rather than waiting, that it ignores runs which say
nothing about how long ingestion takes, and that it stays inside one tenant,
which is the property every figure on this dashboard shares and the one that
would be worst to get wrong.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as sql

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _ingest_run(store, workspace_id, *, queued_s, ran_s, status="succeeded",
                      finished=True, started=True):
    """One ingest run that waited `queued_s` and then worked for `ran_s`."""
    now = datetime.now(timezone.utc)
    created = now - timedelta(seconds=queued_s + ran_s)
    started_at = created + timedelta(seconds=queued_s) if started else None
    finished_at = (started_at + timedelta(seconds=ran_s)) if (finished and started_at) else None

    async with store.file_repo.get_async_session() as session:
        await session.execute(
            sql("""
                INSERT INTO agent_runs
                    (id, run_type, agent_name, status, payload, workspace_id,
                     created_at, started_at, finished_at)
                VALUES
                    (:id, 'ingest_file', 'IngestionAgent', :status, '{}'::jsonb, :ws,
                     :created, :started, :finished)
            """),
            {
                "id": str(uuid.uuid4()), "status": status, "ws": workspace_id,
                "created": created, "started": started_at, "finished": finished_at,
            },
        )
        await session.commit()


async def test_it_measures_working_not_waiting(store, tenant):
    """The whole reason queue time is excluded, in one assertion."""
    ws = await tenant.workspace("Timing")
    # Waited five minutes, worked for ten seconds.
    await _ingest_run(store, ws, queued_s=300, ran_s=10)

    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 1
    assert stats["average_seconds"] == pytest.approx(10, abs=1)


async def test_the_median_is_reported_beside_the_average(store, tenant):
    """One long manual among short memos drags a mean somewhere no document has
    been. The pair is what says whether the average means anything."""
    ws = await tenant.workspace("Spread")
    for seconds in (2, 2, 2, 2, 600):
        await _ingest_run(store, ws, queued_s=1, ran_s=seconds)

    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 5
    assert stats["median_seconds"] == pytest.approx(2, abs=1)
    assert stats["average_seconds"] > 100, "the average should be dragged, that is the point"
    assert stats["slowest_seconds"] == pytest.approx(600, abs=1)


async def test_a_failed_run_is_not_a_processing_time(store, tenant):
    """A failure has a duration and it is the duration of giving up."""
    ws = await tenant.workspace("Failures")
    await _ingest_run(store, ws, queued_s=1, ran_s=5)
    await _ingest_run(store, ws, queued_s=1, ran_s=900, status="failed")

    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 1
    assert stats["average_seconds"] == pytest.approx(5, abs=1)


async def test_a_run_still_going_is_not_counted(store, tenant):
    """Started, not finished. Counting it would report a document as fast
    because it has not got slow yet."""
    ws = await tenant.workspace("InFlight")
    await _ingest_run(store, ws, queued_s=1, ran_s=3)
    await _ingest_run(store, ws, queued_s=1, ran_s=0, status="running", finished=False)

    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 1


async def test_nothing_ingested_reports_nothing_rather_than_zero(store, tenant):
    """"0s" reads as instant. The panel needs to be able to say nothing at all."""
    await tenant.workspace("Empty")
    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 0
    assert stats["average_seconds"] is None
    assert stats["median_seconds"] is None


async def test_another_company_is_not_in_the_number(store, tenant):
    """The property every figure on this dashboard shares, and the one that
    would be worst to get wrong."""
    ws = await tenant.workspace("Ours")
    await _ingest_run(store, ws, queued_s=1, ran_s=4)

    other_owner = await tenant.new_user("other-owner")
    other_org = await store.org_repo.create_organization(other_owner, "Somebody Else")
    other_ws = await store.workspace_repo.create_workspace(
        user_id=other_owner, name="Theirs", organization_id=other_org
    )
    await _ingest_run(store, other_ws, queued_s=1, ran_s=999)

    stats = await store.usage_repo.ingestion_time(tenant.org)
    assert stats["documents"] == 1
    assert stats["average_seconds"] == pytest.approx(4, abs=1)
