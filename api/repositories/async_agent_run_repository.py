"""Async AgentRun repository for managing agent run queue operations."""

from __future__ import annotations

from typing import Any, Dict, Optional
import uuid

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .async_base_repository import AsyncBaseRepository
from ..models import AgentRun as AgentRunORM

logger = logging.getLogger(__name__)


class AsyncAgentRunRepository(AsyncBaseRepository):
    """Async repository for durable agent run enqueuing."""

    async def enqueue_run(
        self,
        *,
        run_type: str,
        agent_name: str,
        agent_version: Optional[str],
        payload: Dict[str, Any],
        user_id: Optional[int] = None,
        workspace_id: Optional[int] = None,
        file_id: Optional[int] = None,
        chat_history_id: Optional[int] = None,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> Optional[str]:
        async with self.get_async_session() as session:
            try:
                run = AgentRunORM(
                    run_type=run_type,
                    agent_name=agent_name,
                    agent_version=agent_version,
                    status="queued",
                    priority=priority,
                    payload=payload,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    chat_history_id=chat_history_id,
                    max_attempts=max_attempts,
                    attempts=0,
                )
                session.add(run)
                await session.flush()
                run_id = str(run.id)
                await session.commit()
                return run_id
            except IntegrityError as e:
                await session.rollback()
                logger.error(f"Integrity error enqueuing agent run: {e}", exc_info=True)
                return None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error enqueuing agent run: {e}", exc_info=True)
                return None

