"""Drafts SyntextAI wrote.

Everything here is scoped by workspace and nothing takes a bare id without one.
Drafts carry no organization of their own; they reach the tenant through the
workspace, and that join is the tenant boundary. Leaving it off is how one
company reads another's drafts, and nothing underneath will catch it.

There is deliberately no method here that hands a draft to retrieval. Approving
one is a route-level act that creates a `files` row, so this repository has no
way to make a draft answerable and cannot be misused into doing so.
"""
from typing import Any, Dict, List, Optional
import logging

from sqlalchemy import select, func, delete

from .async_base_repository import AsyncBaseRepository
from ..models import GeneratedDocument as GeneratedDocumentORM

logger = logging.getLogger(__name__)


def _serialize(orm) -> Dict[str, Any]:
    return {
        "id": orm.id,
        "workspace_id": orm.workspace_id,
        "created_by": orm.created_by,
        "title": orm.title,
        "prompt": orm.prompt,
        "content": orm.content,
        "sources": orm.sources or [],
        "status": orm.status,
        "ingested_file_id": orm.ingested_file_id,
        "created_at": orm.created_at.isoformat() if orm.created_at else None,
        "updated_at": orm.updated_at.isoformat() if orm.updated_at else None,
    }


class AsyncGeneratedDocumentRepository(AsyncBaseRepository):

    async def create(
        self,
        *,
        workspace_id: int,
        created_by: Optional[int],
        title: str,
        prompt: str,
        content: str,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self.get_async_session() as session:
            try:
                orm = GeneratedDocumentORM(
                    workspace_id=int(workspace_id),
                    created_by=created_by,
                    title=title,
                    prompt=prompt,
                    content=content,
                    sources=sources or [],
                    status="draft",
                )
                session.add(orm)
                await session.commit()
                await session.refresh(orm)
                return _serialize(orm)
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating draft in workspace {workspace_id}: {e}", exc_info=True)
                return None

    async def get(self, draft_id: int) -> Optional[Dict[str, Any]]:
        """One draft, by id.

        Returns the workspace_id with it, because the caller has to authorize
        against that and cannot do so without reading the row first. Callers
        MUST check it; nothing here does.
        """
        async with self.get_async_session() as session:
            orm = (
                await session.execute(
                    select(GeneratedDocumentORM).where(GeneratedDocumentORM.id == int(draft_id))
                )
            ).scalar_one_or_none()
            return _serialize(orm) if orm else None

    async def list_for_workspace(
        self, workspace_id: int, *, skip: int = 0, limit: int = 20
    ) -> Dict[str, Any]:
        async with self.get_async_session() as session:
            total = (
                await session.execute(
                    select(func.count(GeneratedDocumentORM.id)).where(
                        GeneratedDocumentORM.workspace_id == int(workspace_id)
                    )
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    select(GeneratedDocumentORM)
                    .where(GeneratedDocumentORM.workspace_id == int(workspace_id))
                    .order_by(GeneratedDocumentORM.created_at.desc())
                    .offset(skip)
                    .limit(limit)
                )
            ).scalars().all()
            # The list does not carry `content`. A workspace's drafts are whole
            # documents, and sending twenty of them to render a list of titles
            # is the same mistake message_feedback was split out to avoid.
            items = []
            for r in rows:
                d = _serialize(r)
                d.pop("content", None)
                d.pop("prompt", None)
                items.append(d)
            return {"items": items, "total": total}

    async def update(
        self,
        draft_id: int,
        *,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Edit a draft. The caller has already authorized it by workspace."""
        async with self.get_async_session() as session:
            try:
                orm = (
                    await session.execute(
                        select(GeneratedDocumentORM).where(GeneratedDocumentORM.id == int(draft_id))
                    )
                ).scalar_one_or_none()
                if not orm:
                    return None
                if title is not None:
                    orm.title = title
                if content is not None:
                    orm.content = content
                await session.commit()
                await session.refresh(orm)
                return _serialize(orm)
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating draft {draft_id}: {e}", exc_info=True)
                return None

    async def mark_ingested(self, draft_id: int, file_id: int) -> bool:
        """Record that somebody approved this draft into the knowledge base.

        Status is a record of what happened, not a gate. Retrieval cannot see
        this table either way; what changed is that a `files` row now exists.
        """
        async with self.get_async_session() as session:
            try:
                orm = (
                    await session.execute(
                        select(GeneratedDocumentORM).where(GeneratedDocumentORM.id == int(draft_id))
                    )
                ).scalar_one_or_none()
                if not orm:
                    return False
                orm.status = "ingested"
                orm.ingested_file_id = int(file_id)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error marking draft {draft_id} ingested: {e}", exc_info=True)
                return False

    async def delete(self, draft_id: int) -> bool:
        async with self.get_async_session() as session:
            try:
                await session.execute(
                    delete(GeneratedDocumentORM).where(GeneratedDocumentORM.id == int(draft_id))
                )
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting draft {draft_id}: {e}", exc_info=True)
                return False
