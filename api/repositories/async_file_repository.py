"""
Asynchronous repository for file-related database operations.

Provides async versions of all file-related DB operations, returning
proper domain models and handling errors consistently.
"""
from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy import select, text, func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm_models import File as FileORM, Segment as SegmentORM, Chunk as ChunkORM

from .async_base_repository import AsyncBaseRepository
from ..models.db_utils import get_async_session
from .domain_models import File, Segment
logger = logging.getLogger(__name__)


class AsyncFileRepository(AsyncBaseRepository[FileORM, Any, Any]):
    """Asynchronous repository for file operations."""

    async def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file and return its ID."""
        async with get_async_session() as session:
            try:
                new_file = FileORM(user_id=user_id, file_name=file_name, file_url=file_url)
                session.add(new_file)
                await session.commit()
                await session.refresh(new_file)
                logger.info(f"Added new file {file_name} (ID: {new_file.id}) for user {user_id}")
                return new_file.id
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error adding file: {e}", exc_info=True)
                return None

    async def update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Store processed file data with segments, chunks, and metadata."""
        async with get_async_session() as session:
            try:
                file = (await session.execute(
                    select(FileORM).where(FileORM.user_id == user_id, FileORM.file_name == filename)
                )).scalar_one_or_none()

                if not file:
                    file = FileORM(user_id=user_id, file_name=filename, file_url="")
                    session.add(file)
                    await session.flush()

                for segment_data in extracted_data:
                    segment = SegmentORM(
                        file_id=file.id,
                        content=segment_data.get("content", ""),
                        page_number=segment_data.get("page_number")
                    )
                    meta_data = {k: v for k, v in segment_data.items() if k not in ["content", "page_number", "chunks"]}
                    if meta_data:
                        segment.meta_data = meta_data

                    session.add(segment)
                    await session.flush()

                    for chunk_data in segment_data.get("chunks", []):
                        chunk = ChunkORM(
                            segment_id=segment.id,
                            content=chunk_data.get("content", ""),
                            embedding=chunk_data.get("embedding")
                        )
                        session.add(chunk)

                await session.commit()
                return True
            except IntegrityError as e:
                await session.rollback()
                logger.error(f"Integrity error updating file with chunks: {e}", exc_info=True)
                return False
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error updating file with chunks: {e}", exc_info=True)
                return False

    async def get_files_for_user(self, user_id: int, skip: int = 0, limit: int = 10) -> Dict[str, Any]:
        """Get paginated files for a user."""
        async with get_async_session() as session:
            try:
                total = await session.scalar(
                    select(func.count()).select_from(FileORM).where(FileORM.user_id == user_id)
                )
                result = await session.execute(
                    select(FileORM)
                    .where(FileORM.user_id == user_id)
                    .order_by(FileORM.created_at.desc())
                    .offset(skip)
                    .limit(limit)
                )
                files = result.scalars().all()
                items = [
                    File(
                        id=f.id,
                        user_id=f.user_id,
                        file_name=f.file_name,
                        file_url=f.file_url,
                        file_type=f.file_type,
                        status=f.processing_status,
                        created_at=f.created_at
                    ) for f in files
                ]
                return {"items": items, "total": total or 0, "page": (skip // limit) + 1, "page_size": limit}
            except SQLAlchemyError as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return {"items": [], "total": 0, "page": 1, "page_size": limit}

    async def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated segments/chunks."""
        async with get_async_session() as session:
            try:
                file = (await session.execute(
                    select(FileORM).where(FileORM.id == file_id, FileORM.user_id == user_id)
                )).scalar_one_or_none()
                if not file:
                    logger.warning(f"File {file_id} not found or not owned by user {user_id}")
                    return False
                await session.delete(file)
                await session.commit()
                logger.info(f"Successfully deleted file {file_id} for user {user_id}")
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
                # fallback manual SQL deletion
                try:
                    await session.execute(text(f"DELETE FROM files WHERE id = :id AND user_id = :user_id"), {"id": file_id, "user_id": user_id})
                    await session.commit()
                    logger.info(f"Deleted file {file_id} using SQL fallback")
                    return True
                except SQLAlchemyError as sql_error:
                    await session.rollback()
                    logger.error(f"SQL fallback error deleting file {file_id}: {sql_error}", exc_info=True)
                    return False

   

    async def query_chunks_by_embedding(
        self,
        user_id: int,
        query_embedding: List[float],
        top_k: int = 5,
        similarity_type: str = "l2"
    ) -> List[Dict]:
        """
        Query chunks by embedding similarity using database-side vector operations.

        Args:
            user_id: ID of the user
            query_embedding: Query vector
            top_k: Number of top results
            similarity_type: 'l2' or 'cosine'

        Returns:
            List of dicts with chunk info and similarity score
        """
        async with get_async_session() as session:
            try:
                # Get all file IDs for this user
                file_ids_query = select(FileORM.id).where(FileORM.user_id == user_id)
                file_ids = (await session.execute(file_ids_query)).scalars().all()
                if not file_ids:
                    return []

                # Build vector similarity expression
                query_vec = query_embedding  # List[float]
                if similarity_type.lower() == "cosine":
                    similarity_expr = func.cosine(ChunkORM.embedding, query_vec)
                    order_by_expr = similarity_expr.desc()
                else:
                    # Default: L2 distance, lower is more similar
                    similarity_expr = func.l2_distance(ChunkORM.embedding, query_vec)
                    order_by_expr = similarity_expr.asc()

                chunks_query = (
                    select(
                        ChunkORM.id,
                        ChunkORM.file_id,
                        ChunkORM.content,
                        similarity_expr.label("similarity")
                    )
                    .where(
                        ChunkORM.file_id.in_(file_ids),
                        ChunkORM.embedding != None
                    )
                    .order_by(order_by_expr)
                    .limit(top_k)
                )

                result = await session.execute(chunks_query)
                chunks = result.all()

                return [
                    {
                        "chunk_id": c.id,
                        "file_id": c.file_id,
                        "content": c.content,
                        "similarity": float(c.similarity)
                    } for c in chunks
                ]

            except SQLAlchemyError as e:
                logger.error(f"Error querying chunks by embedding in DB: {e}", exc_info=True)
                return []


    async def get_segments_for_page(self, file_id: int, page_number: int) -> List[Segment]:
        """Get all segment contents for a specific page."""
        async with get_async_session() as session:
            try:
                segments = (await session.execute(
                    select(SegmentORM).where(SegmentORM.file_id == file_id, SegmentORM.page_number == page_number)
                )).scalars().all()
                return [Segment(id=s.id, file_id=s.file_id, content=s.content, page_number=s.page_number, meta_data=s.meta_data) for s in segments]
            except SQLAlchemyError as e:
                logger.error(f"Error getting segments for page: {e}", exc_info=True)
                return []

    async def get_segments_for_time_range(self, file_id: int, start_time: float, end_time: Optional[float] = None) -> List[Segment]:
        """Get segments for a specific time range of a video file."""
        async with get_async_session() as session:
            try:
                query = select(SegmentORM).where(SegmentORM.file_id == file_id)
                if end_time:
                    query = query.where(
                        SegmentORM.meta_data['start_time'].astext.cast(float) <= end_time,
                        SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                    )
                else:
                    query = query.where(
                        SegmentORM.meta_data['start_time'].astext.cast(float) <= start_time,
                        SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                    )
                segments = (await session.execute(query)).scalars().all()
                return [Segment(id=s.id, file_id=s.file_id, content=s.content, page_number=s.page_number, meta_data=s.meta_data) for s in segments]
            except SQLAlchemyError as e:
                logger.error(f"Error getting segments for time range: {e}", exc_info=True)
                return []

    async def get_file_by_id(self, file_id: int) -> Optional[File]:
        """Get a file record by ID."""
        async with get_async_session() as session:
            try:
                file = (await session.execute(select(FileORM).where(FileORM.id == file_id))).scalar_one_or_none()
                if file:
                    return File(id=file.id, user_id=file.user_id, file_name=file.file_name, file_url=file.file_url,
                                file_type=file.file_type, status=file.processing_status, created_at=file.created_at)
                return None
            except SQLAlchemyError as e:
                logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
                return None

    async def get_file_by_name(self, user_id: int, filename: str) -> Optional[File]:
        """Get a file by user ID and filename."""
        async with get_async_session() as session:
            try:
                file = (await session.execute(
                    select(FileORM).where(FileORM.user_id == user_id, FileORM.file_name == filename)
                )).scalar_one_or_none()
                if file:
                    return File(id=file.id, user_id=file.user_id, file_name=file.file_name, file_url=file.file_url,
                                file_type=file.file_type, status=file.processing_status, created_at=file.created_at)
                return None
            except SQLAlchemyError as e:
                logger.error(f"Error getting file by name: {e}", exc_info=True)
                return None

    async def check_user_file_ownership(self, file_id: int, user_id: int) -> bool:
        """Check if a user owns a specific file."""
        async with get_async_session() as session:
            try:
                exists = await session.scalar(
                    select(FileORM.id).where(FileORM.id == file_id, FileORM.user_id == user_id).exists()
                )
                return bool(exists)
            except SQLAlchemyError as e:
                logger.error(f"Error checking file ownership for file {file_id}, user {user_id}: {e}", exc_info=True)
                return False
