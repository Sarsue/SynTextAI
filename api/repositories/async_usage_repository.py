"""What a company can see about its own use of this product.

WHY THIS IS NOT POSTHOG

PostHog is set up and receives events, but the key configured here is a project
key (`phc_...`), which writes. Reading events back needs a personal API key with
query scope, which does not exist, and would add a second secret plus an
external dependency that can be down or rate limited.

More importantly it would be the wrong source. PostHog holds page views and
clicks; what an owner wants to know is how much their team is asking, which
documents answer nothing, and which answers were wrong. All of that is already
in this database, one query each, and it is already scoped by organization,
which is what keeps one customer's usage out of another's dashboard.

PostHog remains the right tool for the *other* dashboard, the one about all
customers rather than one, and that one needs no code because PostHog has a UI.

EVERY QUERY HERE JOINS THROUGH THE WORKSPACE

A document, a conversation and a rating all reach their organization the same
way: through the workspace they belong to. There is no organization_id on those
tables, so the join is the tenancy boundary, and leaving it off is how a
dashboard shows somebody else's numbers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import text

from .async_base_repository import AsyncBaseRepository

logger = logging.getLogger(__name__)

# The window every count covers. A month is what a person compares against
# their own last month; a lifetime total only ever goes up and says nothing.
DEFAULT_DAYS = 30


class AsyncUsageRepository(AsyncBaseRepository):
    """Aggregates for one organization's own dashboard."""

    async def questions_asked(self, organization_id: int, days: int = DEFAULT_DAYS) -> int:
        async with self.get_async_session() as session:
            return (await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM messages m
                    JOIN chat_histories h ON h.id = m.chat_history_id
                    JOIN workspaces w ON w.id = h.workspace_id
                    WHERE m.sender = 'user'
                      AND w.organization_id = :org
                      AND m.timestamp >= now() - make_interval(days => :days)
                    """
                ),
                {"org": int(organization_id), "days": int(days)},
            )).scalar() or 0

    async def most_active_people(
        self, organization_id: int, days: int = DEFAULT_DAYS, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Who is actually using it.

        Shown to an owner deciding whether the subscription is earning its
        place, and the honest version of that answer includes the people who
        have asked nothing, which is why the count is per person rather than a
        single average.
        """
        async with self.get_async_session() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT u.email, count(*) AS questions
                    FROM messages m
                    JOIN chat_histories h ON h.id = m.chat_history_id
                    JOIN workspaces w ON w.id = h.workspace_id
                    JOIN users u ON u.id = m.user_id
                    WHERE m.sender = 'user'
                      AND w.organization_id = :org
                      AND m.timestamp >= now() - make_interval(days => :days)
                    GROUP BY u.email
                    ORDER BY questions DESC
                    LIMIT :limit
                    """
                ),
                {"org": int(organization_id), "days": int(days), "limit": int(limit)},
            )
            return [{"email": e, "questions": q} for e, q in rows.all()]

    async def documents_by_status(self, organization_id: int) -> Dict[str, int]:
        """Including the failures, which is the point.

        A document that failed to ingest is invisible in the file list unless
        somebody scrolls to it, and it is the single most likely reason an
        answer is missing something the customer knows they uploaded.
        """
        async with self.get_async_session() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT f.processing_status, count(*)
                    FROM files f
                    JOIN workspaces w ON w.id = f.workspace_id
                    WHERE w.organization_id = :org
                    GROUP BY f.processing_status
                    """
                ),
                {"org": int(organization_id)},
            )
            return {status: count for status, count in rows.all()}

    async def answers_rated(
        self, organization_id: int, days: int = DEFAULT_DAYS
    ) -> Dict[str, Any]:
        """Thumbs, and the reasons behind the thumbs-down.

        This is the one an owner and we both want. The chips are countable, so
        "nine of eleven complaints were wrong_source" is a sentence this can
        produce, and that is a different fix from "the answer was incomplete".
        """
        async with self.get_async_session() as session:
            totals = (await session.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE fb.rating = 1) AS helpful,
                      count(*) FILTER (WHERE fb.rating = -1) AS unhelpful
                    FROM message_feedback fb
                    JOIN messages m ON m.id = fb.message_id
                    JOIN chat_histories h ON h.id = m.chat_history_id
                    JOIN workspaces w ON w.id = h.workspace_id
                    WHERE w.organization_id = :org
                      AND fb.created_at >= now() - make_interval(days => :days)
                    """
                ),
                {"org": int(organization_id), "days": int(days)},
            )).first()

            reasons = await session.execute(
                text(
                    """
                    SELECT fb.reason, count(*)
                    FROM message_feedback fb
                    JOIN messages m ON m.id = fb.message_id
                    JOIN chat_histories h ON h.id = m.chat_history_id
                    JOIN workspaces w ON w.id = h.workspace_id
                    WHERE w.organization_id = :org
                      AND fb.rating = -1
                      AND fb.reason IS NOT NULL
                      AND fb.created_at >= now() - make_interval(days => :days)
                    GROUP BY fb.reason
                    ORDER BY count(*) DESC
                    """
                ),
                {"org": int(organization_id), "days": int(days)},
            )

            return {
                "helpful": (totals[0] if totals else 0) or 0,
                "unhelpful": (totals[1] if totals else 0) or 0,
                "reasons": [{"reason": r, "count": c} for r, c in reasons.all()],
            }

    async def documents_never_retrieved(
        self, organization_id: int, limit: int = 10
    ) -> List[str]:
        """Documents that have never contributed to an answer.

        Not "unread": a document nothing ever retrieves is usually one that
        extracted badly, or one nobody has thought to ask about. Both are worth
        an owner's attention and neither is visible anywhere today.

        **Empty until there is something to compare against.** The file ids
        behind this are recorded on each run from 2026-08-11 onward, so every
        run before that has none. Answering from those would mark every
        document in the account as never used, which is both alarming and
        false. So this returns nothing at all until at least one run carries
        the ids, and only then starts naming documents.
        """
        async with self.get_async_session() as session:
            has_data = (await session.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM agent_runs r
                      JOIN workspaces w ON w.id = r.workspace_id
                      WHERE w.organization_id = :org
                        AND jsonb_typeof(r.result -> 'cited_file_ids') = 'array'
                        -- Non-empty, and this is the whole guard. A run that
                        -- recorded the key but no ids proves nothing about any
                        -- document, and treating it as evidence switched the
                        -- metric on with nothing behind it: every document in
                        -- the account was then listed as never used, which is
                        -- the exact false alarm this method exists to avoid.
                        -- Caught by looking at the panel rather than the test.
                        AND jsonb_array_length(r.result -> 'cited_file_ids') > 0
                    )
                    """
                ),
                {"org": int(organization_id)},
            )).scalar()
            if not has_data:
                return []

            rows = await session.execute(
                text(
                    """
                    SELECT f.file_name
                    FROM files f
                    JOIN workspaces w ON w.id = f.workspace_id
                    WHERE w.organization_id = :org
                      AND f.processing_status = 'processed'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM agent_runs r
                        CROSS JOIN LATERAL jsonb_array_elements(
                          r.result -> 'cited_file_ids'
                        ) AS cited
                        WHERE r.workspace_id = f.workspace_id
                          AND jsonb_typeof(r.result -> 'cited_file_ids') = 'array'
                          AND cited::text = f.id::text
                      )
                    ORDER BY f.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"org": int(organization_id), "limit": int(limit)},
            )
            return [name for (name,) in rows.all()]
