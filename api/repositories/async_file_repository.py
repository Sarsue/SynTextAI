"""
Async File repository for managing file-related database operations.
"""
from typing import Optional, List, Dict, Any
import logging
from ..core.utils import sanitize_extracted_text
import asyncio

from .async_base_repository import AsyncBaseRepository
from ..models import File as FileORM, Chunk as ChunkORM
from ..models import Segment as SegmentORM

# Import SQLAlchemy async components
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.exc import IntegrityError

import json
import os
import requests

from api.core.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)

class AsyncFileRepository(AsyncBaseRepository):
    """Async repository for file operations."""

    def __init__(self, database_url: str = None):
        """Initialize the async file repository.

        Args:
            database_url: Database connection URL. If None, uses environment variable.
        """
        super().__init__(database_url)

    async def add_file(self, user_id: int, file_name: str, file_url: str, file_size_bytes: Optional[int] = None, workspace_id: Optional[int] = None) -> Optional[int]:
        """Add a new file to the database.

        Args:
            user_id: ID of the user who owns this file
            file_name: Name of the file
            file_url: URL where the file is stored
            file_size_bytes: Size of the file in bytes
            workspace_id: ID of the workspace this file belongs to

        Returns:
            int: The ID of the newly created file, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                file_orm = FileORM(
                    user_id=user_id,
                    file_name=file_name,
                    file_url=file_url,
                    processing_status="uploaded",  # Explicitly set status to ensure it's not None
                    file_size_bytes=file_size_bytes,
                    workspace_id=workspace_id,
                )
                session.add(file_orm)
                await session.flush()
                file_id = file_orm.id
                await session.commit()
                logger.info(f"Added new file {file_name} (ID: {file_id}) for user {user_id}")
                return file_id
            except IntegrityError as e:
                await session.rollback()
                logger.error(f"Integrity error adding file {file_name}: {e}", exc_info=True)
                return None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding file {file_name}: {e}", exc_info=True)
                return None

    async def update_file_with_chunks(
        self,
        user_id: int,
        filename: str,
        file_type: str,
        extracted_data: List[Dict],
        file_id: Optional[int] = None,
    ) -> bool:
        """Store processed file data with embeddings, segments, and metadata.

        Args:
            user_id: ID of the user who owns the file
            filename: Name of the file
            file_type: Type of file (pdf, video, etc.)
            extracted_data: Processed data containing chunks and embeddings

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            file = None
            try:
                # Get or create the file record
                if file_id is not None:
                    file = await session.get(FileORM, int(file_id))
                else:
                    # Filename is not unique (e.g. repeated YouTube URLs). Select newest match.
                    stmt = (
                        select(FileORM)
                        .where(and_(FileORM.user_id == user_id, FileORM.file_name == filename))
                        .order_by(FileORM.created_at.desc())
                        .limit(1)
                    )
                    result = await session.execute(stmt)
                    file = result.scalars().first()

                if not file:
                    file = FileORM(
                        user_id=user_id,
                        file_name=filename,
                        file_url="",
                        file_type=file_type,
                        processing_status="processed"
                    )
                    session.add(file)
                    await session.flush()
                else:
                    file.file_type = file_type
                    file.processing_status = "processed"

                # Every processor emits a flat list of retrieval units:
                #   {'text': str, 'page_num': int, 'embedding': list[float], ...}
                #
                # This previously read them as segments carrying nested chunks,
                # a shape nothing produces. The result was silent and total: each
                # unit became a segment whose content was '' because the text is
                # under 'text' not 'content', the real text and the embedding
                # were swept into meta_data, and because no item had a 'chunks'
                # key, zero chunks were written. Files were marked processed with
                # nothing searchable behind them, so retrieval matched nothing
                # and chat could not answer from any document ever uploaded.
                #
                # Retrieval joins chunks to segments, taking the vector from the
                # chunk and the text from the segment, so each unit must produce
                # exactly one of each.
                expected = 0
                # Grouped by page, because the page is the citation unit and
                # the chunks under it are the retrieval units. These used to be
                # the same object: one segment and one chunk per unit, so a
                # page-sized chunk was cited and retrieved as one thing. The
                # 400-token splitter now produces two or three chunks per page,
                # and all of them must hang off that page's single segment or a
                # citation would name a fragment rather than something a reader
                # can open.
                by_page: Dict[Any, List[Dict[str, Any]]] = {}
                order: List[Any] = []
                for unit in extracted_data:
                    key = unit.get('page_num') or unit.get('page_number')
                    if key not in by_page:
                        by_page[key] = []
                        order.append(key)
                    by_page[key].append(unit)

                for page_key in order:
                    units = by_page[page_key]
                    # Sanitized here as well as at extraction: any processor can
                    # produce a byte Postgres will not take, and one such byte
                    # fails the whole transaction and marks a perfectly good
                    # document as failed.
                    #
                    # The page's own text where the processor supplied it.
                    # Joining the chunks back together would repeat their
                    # overlap, so the page would read with duplicated sentences
                    # everywhere a chunk boundary fell.
                    page_text = sanitize_extracted_text(
                        units[0].get('page_text')
                        or "\n".join(u.get('text') or u.get('content') or '' for u in units)
                    )
                    chunk_units = [
                        u for u in units
                        if sanitize_extracted_text(u.get('text') or u.get('content') or '').strip()
                    ]
                    if not page_text.strip() or not chunk_units:
                        continue

                    meta = {
                        k: v for k, v in units[0].items()
                        if k not in ('text', 'content', 'page_num', 'page_number',
                                     'embedding', 'chunks', 'context', 'page_text')
                    }
                    segment = SegmentORM(
                        file_id=file.id,
                        content=page_text,
                        context=(units[0].get('context') or None),
                        page_number=page_key,
                    )
                    if meta:
                        segment.meta_data = meta
                    session.add(segment)
                    await session.flush()

                    for u in chunk_units:
                        embedding = u.get('embedding')
                        if embedding is None:
                            # A chunk with no vector can never be retrieved, so
                            # it is not a silent partial success.
                            logger.error(f"Chunk for {filename} has no embedding; aborting store")
                            await session.rollback()
                            return False

                        session.add(ChunkORM(
                            file_id=file.id,
                            segment_id=segment.id,
                            content=sanitize_extracted_text(
                                u.get('text') or u.get('content') or ''
                            ),
                            embedding=embedding,
                        ))
                        expected += 1

                await session.commit()

                # Assert the rows actually landed. The original failure reported
                # success while writing nothing retrievable, so trust the
                # database rather than the absence of an exception.
                stored = (await session.execute(
                    select(func.count(ChunkORM.id)).where(ChunkORM.file_id == file.id)
                )).scalar() or 0
                if stored < expected:
                    logger.error(
                        f"Stored {stored} chunks for {filename} but expected {expected}"
                    )
                    return False

                logger.info(f"Stored {stored} chunks and segments for {filename} (ID: {file.id})")
                return True
            except IntegrityError as e:
                await session.rollback()
                error_msg = f"Integrity error updating file {filename} with chunks: {str(e)[:1000]}"
                logger.error(error_msg, exc_info=True)
                if file:
                    file.processing_status = "failed"
                    await session.commit()
                return False
            except Exception as e:
                await session.rollback()
                error_msg = f"Error updating file {filename} with chunks: {str(e)[:1000]}"
                logger.error(error_msg, exc_info=True)
                if file:
                    file.processing_status = "failed"
                    await session.commit()
                return False

    async def get_files_for_user(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 10,
        workspace_id: int = None,
        accessible_workspace_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Get paginated files visible to a user.

        Visibility follows the *workspace*, not the uploader. This previously
        filtered on files.user_id, so an invited staff member saw none of the
        workspace's documents: the rows belong to whoever uploaded them, which
        is normally the owner. Files with no workspace stay private to their
        uploader, which is how pre-workspace uploads behave.

        Args:
            user_id: ID of the requesting user
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return (for pagination)
            workspace_id: Restrict to a single workspace. The caller must have
                already authorized access to it.
            accessible_workspace_ids: Every workspace the user may read. Required
                to see workspaces owned by someone else when workspace_id is not
                given.

        Returns:
            Dict: {
                'items': List[Dict],  # List of file records with metadata
                'total': int,         # Total number of visible files
                'page': int,          # Current page number (1-based)
                'page_size': int      # Number of items per page
            }
        """
        async with self.get_async_session() as session:
            try:
                # Build base query conditions
                if workspace_id is not None:
                    # Caller authorized this workspace, so scope purely to it.
                    conditions = [FileORM.workspace_id == workspace_id]
                elif accessible_workspace_ids:
                    # Everything in the user's workspaces, plus their own
                    # workspace-less files.
                    conditions = [
                        or_(
                            FileORM.workspace_id.in_(accessible_workspace_ids),
                            and_(FileORM.workspace_id.is_(None), FileORM.user_id == user_id),
                        )
                    ]
                else:
                    conditions = [FileORM.user_id == user_id]
                
                # Get total count
                stmt = select(func.count(FileORM.id)).where(*conditions)
                result = await session.execute(stmt)
                total = result.scalar() or 0

                # Get paginated results
                stmt = select(
                    FileORM.id,
                    FileORM.file_name,
                    FileORM.file_url,
                    FileORM.created_at,
                    FileORM.processing_status,
                    FileORM.file_type,
                    FileORM.workspace_id
                ).where(*conditions).order_by(FileORM.created_at.desc()).offset(skip).limit(limit)
                result = await session.execute(stmt)
                files = result.fetchall()

                items = [
                    {
                        "id": file.id,
                        "file_name": file.file_name,
                        "name": file.file_name,
                        "file_url": file.file_url,
                        "publicUrl": file.file_url,
                        "processing_status": file.processing_status,
                        "file_type": file.file_type,
                        "workspace_id": file.workspace_id,
                        "created_at": file.created_at.isoformat() if file.created_at else None
                    }
                    for file in files
                ]

                return {
                    'items': items,
                    'total': total,
                    'page': (skip // limit) + 1,
                    'page_size': limit
                }

            except Exception as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return {'items': [], 'total': 0, 'page': 1, 'page_size': limit}

    async def count_files_for_user(self, user_id: int) -> int:
        """Return the total number of files owned by a user."""
        async with self.get_async_session() as session:
            try:
                stmt = select(func.count(FileORM.id)).where(FileORM.user_id == user_id)
                result = await session.execute(stmt)
                return int(result.scalar() or 0)
            except Exception as e:
                logger.error(f"Error counting files for user {user_id}: {e}", exc_info=True)
                return 0

    async def total_storage_bytes_for_user(self, user_id: int) -> int:
        """Return total recorded storage usage in bytes for a user based on file_size_bytes."""
        async with self.get_async_session() as session:
            try:
                stmt = select(func.coalesce(func.sum(FileORM.file_size_bytes), 0)).where(FileORM.user_id == user_id)
                result = await session.execute(stmt)
                total = result.scalar()
                return int(total or 0)
            except Exception as e:
                logger.error(f"Error summing storage bytes for user {user_id}: {e}", exc_info=True)
                return 0

    async def count_files_for_workspace(self, workspace_id: int) -> int:
        """Return the total number of files in a workspace, across all members.

        Free-plan limits apply to the organization, so a shared workspace has one
        allowance rather than one per member.
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(func.count(FileORM.id)).where(FileORM.workspace_id == workspace_id)
                result = await session.execute(stmt)
                return int(result.scalar() or 0)
            except Exception as e:
                logger.error(f"Error counting files for workspace {workspace_id}: {e}", exc_info=True)
                return 0

    async def total_storage_bytes_for_workspace(self, workspace_id: int) -> int:
        """Return total recorded storage usage in bytes for a workspace."""
        async with self.get_async_session() as session:
            try:
                stmt = select(func.coalesce(func.sum(FileORM.file_size_bytes), 0)).where(
                    FileORM.workspace_id == workspace_id
                )
                result = await session.execute(stmt)
                return int(result.scalar() or 0)
            except Exception as e:
                logger.error(f"Error summing storage bytes for workspace {workspace_id}: {e}", exc_info=True)
                return 0

    async def delete_file_entry(self, file_id: int) -> bool:
        """Delete a file and all associated data.

        Authorization is the route's job, not this method's. Matching on
        user_id here meant deletion was scoped to whoever happened to upload the
        document: an owner could not remove one an admin had added, and a
        document left behind by a deleted account carries user_id NULL, so it
        matched nobody and could never be deleted at all.

        Args:
            file_id: ID of the file to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file_obj = result.scalar_one_or_none()

                if not file_obj:
                    logger.warning(f"File {file_id} not found")
                    return False

                # Delete the file (cascade should handle related entities)
                await session.delete(file_obj)
                await session.commit()
                logger.info(f"Successfully deleted file {file_id} with cascade")
                return True

            except Exception as e:
                await session.rollback()
                error_msg = f"Error deleting file {file_id}: {str(e)[:1000]}"
                logger.error(error_msg, exc_info=True)
                try:
                    await session.execute(text("DELETE FROM chunks WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM segments WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM files WHERE id = :file_id"), {"file_id": file_id})
                    await session.commit()
                    logger.info(f"Successfully deleted file {file_id} using manual SQL deletion")
                    return True
                except Exception as sql_error:
                    error_msg = f"SQL fallback error deleting file {file_id}: {str(sql_error)[:1000]}"
                    logger.error(error_msg, exc_info=True)
                    return False

    # query_chunks_by_embedding used to live here. Nothing called it. It
    # predated pgvector: it selected every chunk belonging to a user, pulled all
    # of their embeddings into Python, and computed cosine distance in a loop
    # with scipy. hybrid_search does the same job in the database, against an
    # index, scoped by workspace rather than by uploader. Deleting it also took
    # numpy and scipy out of the API's dependencies, which were carried solely
    # for those four lines.

    # --- Hybrid Search (vector + BM25 via Postgres full text) ---
    DEFAULT_VECTOR_WEIGHT = 0.7
    DEFAULT_BM25_WEIGHT = 0.3
    DEFAULT_TOP_K = 10
    # How deep each of the two searches goes before the results are fused. Has
    # to exceed top_k by enough that a chunk ranked well by one search and
    # poorly by the other still survives to be fused.
    CANDIDATE_POOL = 100

    async def hybrid_search(
        self,
        user_id: int,
        query: str,
        query_embedding: List[float],
        workspace_id: Optional[int] = None,
        file_id: Optional[int] = None,
        vector_weight: float = None,
        bm25_weight: float = None,
        top_k: int = None,
        accessible_workspace_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid search combining vector similarity and BM25 in Postgres.

        Retrieval is scoped by *workspace*, not by who uploaded the documents.
        The previous `f.user_id = :user_id` clause meant an invited staff member
        retrieved zero chunks and therefore got no answers at all, because the
        rows belong to the owner who uploaded them.

        Returns a list of { chunk_id, file_id, content, hybrid_score }.
        """
        vw = self.DEFAULT_VECTOR_WEIGHT if vector_weight is None else float(vector_weight)
        bw = self.DEFAULT_BM25_WEIGHT if bm25_weight is None else float(bm25_weight)
        k = self.DEFAULT_TOP_K if top_k is None else int(top_k)

        async with self.get_async_session() as session:
            try:
                if workspace_id is not None:
                    # Caller authorized this workspace, so scope purely to it.
                    where_clauses = ["f.workspace_id = :workspace_id"]
                elif accessible_workspace_ids:
                    # Everything in the user's workspaces, plus their own
                    # workspace-less files.
                    where_clauses = [
                        "(f.workspace_id = ANY(:accessible_workspace_ids)"
                        " OR (f.workspace_id IS NULL AND f.user_id = :user_id))"
                    ]
                else:
                    where_clauses = ["f.user_id = :user_id"]

                if file_id is not None:
                    where_clauses.append("f.id = :file_id")

                where_sql = " AND ".join(where_clauses)
                sql = text(
                    """
                    WITH query AS (
                      SELECT
                        CAST(:embedding AS vector) AS embedding,
                        -- 'english' stems and drops stopwords; 'simple' did
                        -- neither, so "what goes in an executive summary"
                        -- became 'what'&'goes'&'in'&'an'&'executive'&'summary'
                        -- and matched zero rows in the whole workspace. The
                        -- replace turns the AND that plainto_tsquery builds
                        -- into an OR, so a question ranks by how many of its
                        -- terms a page contains instead of needing all of them.
                        REPLACE(
                          plainto_tsquery('english', :keywords)::text, ' & ', ' | '
                        )::tsquery AS keywords
                    ),
                    -- Two searches, each in the shape its index can answer,
                    -- rather than one blended expression no index can.
                    --
                    -- The previous version ordered by
                    --   0.7 * (1 - cosine) + 0.3 * ts_rank_cd(...)
                    -- which is not a distance, so Postgres had no choice but to
                    -- scan every chunk in the workspace, recompute to_tsvector
                    -- over text that had not changed since upload, and sort the
                    -- lot: 171ms for 316 chunks, and linear from there.
                    vec AS (
                      SELECT c.id AS chunk_id,
                             ROW_NUMBER() OVER (ORDER BY c.embedding <=> q.embedding) AS rank
                      FROM chunks c
                      JOIN files f ON f.id = c.file_id
                      CROSS JOIN query q
                      WHERE """ + where_sql + """
                      ORDER BY c.embedding <=> q.embedding
                      LIMIT :candidates
                    ),
                    txt AS (
                      SELECT c.id AS chunk_id,
                             ROW_NUMBER() OVER (
                               ORDER BY ts_rank_cd(c.tsv, q.keywords, 32) DESC
                             ) AS rank
                      FROM segments s
                      JOIN chunks c ON c.segment_id = s.id
                      JOIN files f ON f.id = s.file_id
                      CROSS JOIN query q
                      WHERE """ + where_sql + """ AND c.tsv @@ q.keywords
                      ORDER BY ts_rank_cd(c.tsv, q.keywords, 32) DESC
                      LIMIT :candidates
                    ),
                    -- Reciprocal rank fusion. Ranks are comparable across the
                    -- two lists in a way the raw scores never were: a cosine
                    -- similarity and a ts_rank_cd share no scale, and the old
                    -- 0.7/0.3 blend of them was arithmetic on incomparable
                    -- units. Position is the only thing both lists agree on.
                    -- The constant 60 is the usual one; it stops the top result
                    -- of either list dominating the other outright.
                    fused AS (
                      SELECT chunk_id, SUM(weight / (60 + rank)) AS hybrid_score
                      FROM (
                        -- Cast explicitly: asyncpg sends a bare parameter as
                        -- text, and "text / bigint" is not an operator, so the
                        -- fusion failed at runtime while working fine in psql
                        -- where the same expression had a literal in it.
                        SELECT chunk_id, rank,
                               CAST(:vector_weight AS double precision) AS weight FROM vec
                        UNION ALL
                        SELECT chunk_id, rank,
                               CAST(:bm25_weight AS double precision) AS weight FROM txt
                      ) ranked
                      GROUP BY chunk_id
                    )
                    SELECT
                      c.id AS id,
                      c.file_id AS file_id,
                      c.segment_id AS segment_id,
                      COALESCE(c.content, '') AS content,
                      f.file_name AS file_name,
                      f.file_url AS file_url,
                      s.page_number AS page_number,
                      s.meta_data AS meta_data,
                      fused.hybrid_score AS hybrid_score
                    FROM fused
                    JOIN chunks c ON c.id = fused.chunk_id
                    JOIN files f ON f.id = c.file_id
                    LEFT JOIN segments s ON s.id = c.segment_id
                    ORDER BY fused.hybrid_score DESC
                    LIMIT :top_k
                    """
                )

                # pgvector's text input format, not a Python list. asyncpg binds
                # this parameter as text for CAST(... AS vector), so a list is
                # rejected outright with "expected str, got list" and every
                # search raised before touching the index.
                embedding_literal = "[" + ",".join(str(float(x)) for x in (query_embedding or [])) + "]"

                params = {
                    "embedding": embedding_literal,
                    "keywords": query,
                    "vector_weight": vw,
                    "bm25_weight": bw,
                    "top_k": k,
                    "candidates": max(self.CANDIDATE_POOL, k * 4),
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "file_id": file_id,
                    "accessible_workspace_ids": list(accessible_workspace_ids or []),
                }

                result = await session.execute(sql, params)
                rows = result.fetchall()
                out: List[Dict[str, Any]] = []
                for row in rows:
                    out.append({
                        "chunk_id": row.id,
                        "file_id": row.file_id,
                        "segment_id": row.segment_id,
                        "content": row.content,
                        "file_name": row.file_name,
                        "file_url": row.file_url,
                        "page_number": row.page_number,
                        "meta_data": row.meta_data if row.meta_data is not None else {},
                        "hybrid_score": float(row.hybrid_score) if row.hybrid_score is not None else 0.0,
                    })
                return out
            except Exception as e:
                logger.error(f"Error performing hybrid_search: {e}", exc_info=True)
                return []

    async def set_outline(self, file_id: int, outline: List[Dict[str, Any]]) -> bool:
        """Store what the document says it contains."""
        async with self.get_async_session() as session:
            try:
                await session.execute(
                    text("UPDATE files SET outline = CAST(:outline AS jsonb) WHERE id = :fid"),
                    {"outline": json.dumps(outline), "fid": int(file_id)},
                )
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not store outline for file {file_id}: {e}")
                return False

    async def get_outline(self, file_id: int) -> List[Dict[str, Any]]:
        async with self.get_async_session() as session:
            try:
                row = (await session.execute(
                    text("SELECT outline FROM files WHERE id = :fid"), {"fid": int(file_id)}
                )).first()
                return list(row[0]) if row and row[0] else []
            except Exception as e:
                logger.error(f"Could not read outline for file {file_id}: {e}")
                return []

    async def get_file_pages(self, file_id: int) -> List[Dict[str, Any]]:
        """Every page of a document, in order, as extracted.

        The segment is the page, so this is the page text and nothing else: not
        the chunks, which overlap and would repeat sentences at every boundary.
        """
        async with self.get_async_session() as session:
            try:
                rows = (await session.execute(
                    text("""SELECT page_number, content FROM segments
                            WHERE file_id = :fid
                            ORDER BY page_number NULLS LAST, id"""),
                    {"fid": int(file_id)},
                )).all()
                return [
                    {"page_number": r[0], "content": r[1] or ""}
                    for r in rows
                ]
            except Exception as e:
                logger.error(f"Could not read pages for file {file_id}: {e}")
                return []

    async def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file.

        Args:
            file_id: ID of the file
            page_number: Page number to retrieve segments for

        Returns:
            List[Dict]: List of segments
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SegmentORM).where(
                    and_(SegmentORM.file_id == file_id, SegmentORM.page_number == page_number)
                )
                result = await session.execute(stmt)
                segments = result.scalars().all()

                result = []
                for segment in segments:
                    meta = segment.meta_data or {}
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'meta_data': meta
                    })
                return result
            except Exception as e:
                logger.error(f"Error getting segments for page {page_number}: {e}", exc_info=True)
                return []

    async def get_segments_for_time_range(
        self,
        file_id: int,
        start_time: float,
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get segment contents for a specific time range of a video file.

        Args:
            file_id: ID of the file
            start_time: Start time in seconds
            end_time: End time in seconds (optional)

        Returns:
            List[Dict]: List of segments within the time range
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SegmentORM).where(SegmentORM.file_id == file_id)
                if end_time:
                    stmt = stmt.filter(
                        and_(
                            SegmentORM.meta_data['start_time'].astext.cast(float) <= end_time,
                            SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                        )
                    )
                else:
                    stmt = stmt.filter(
                        and_(
                            SegmentORM.meta_data['start_time'].astext.cast(float) <= start_time,
                            SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                        )
                    )
                result = await session.execute(stmt)
                segments = result.scalars().all()

                result = []
                for segment in segments:
                    meta = segment.meta_data or {}
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'meta_data': meta
                    })
                return result
            except Exception as e:
                logger.error(f"Error getting segments for time range: {e}", exc_info=True)
                return []

    async def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file record by ID.

        Args:
            file_id: ID of the file

        Returns:
            Dict: File record if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()
                if file_orm:
                    return {
                        'id': file_orm.id,
                        'user_id': file_orm.user_id,
                        'file_name': file_orm.file_name,
                        'file_url': file_orm.file_url,
                        'file_type': file_orm.file_type,
                        # Access to a document is decided by its workspace,
                        # not by who uploaded it, so callers that authorize a
                        # read need this here.
                        'workspace_id': file_orm.workspace_id,
                        'processing_status': file_orm.processing_status,
                        'created_at': file_orm.created_at.isoformat() if file_orm.created_at else None
                    }
                return None
            except Exception as e:
                logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
                return None
    
    async def update_file_workspace(
        self, file_id: int, workspace_id: int, file_url: Optional[str] = None
    ) -> bool:
        """Move a file to another workspace.

        Args:
            file_id: ID of the file to update
            workspace_id: New workspace ID
            file_url: New stored location. Passed together with the workspace
                because the object physically moves with it, and a row whose
                url points at the old workspace's folder would break the
                invariant that a document's path names the workspace it is in.

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file = result.scalar_one_or_none()
                
                if not file:
                    logger.warning(f"File {file_id} not found for workspace update")
                    return False
                
                file.workspace_id = workspace_id
                if file_url:
                    file.file_url = file_url
                await session.commit()
                logger.info(f"Updated file {file_id} workspace to {workspace_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating file {file_id} workspace: {e}", exc_info=True)
                return False

    async def file_name_exists_in_workspace(self, workspace_id: int, file_name: str) -> bool:
        """Is a document by this name already in this workspace?

        Workspace-scoped, not user-scoped. get_file_by_name asks whether *you*
        have a file by that name, which is the wrong question in a folder
        several people share: it would let one person's upload land on top of a
        name somebody else had already used.
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM.id).where(
                    and_(
                        FileORM.workspace_id == workspace_id,
                        FileORM.file_name == file_name,
                    )
                ).limit(1)
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None
            except Exception as e:
                logger.error(
                    f"Error checking name {file_name} in workspace {workspace_id}: {e}",
                    exc_info=True,
                )
                # Say no rather than blocking an upload on a failed lookup. The
                # id in the object path means a duplicate that slips through
                # costs a confusing list entry, not a lost document.
                return False

    async def set_file_url(self, file_id: int, file_url: str) -> bool:
        """Record where a file's bytes ended up.

        Upload happens after the row exists, because the object path is keyed by
        the row id to keep two uploads of the same filename from colliding
        inside a shared workspace folder. The row is therefore briefly urlless,
        and this closes that gap.
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file = result.scalar_one_or_none()

                if not file:
                    logger.warning(f"File {file_id} not found for url update")
                    return False

                file.file_url = file_url
                await session.commit()
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error setting url for file {file_id}: {e}", exc_info=True)
                return False

    async def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename.

        Args:
            user_id: ID of the user
            filename: Name of the file

        Returns:
            Dict: File record if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(
                    and_(FileORM.user_id == user_id, FileORM.file_name == filename)
                )
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()
                if file_orm:
                    return {
                        'id': file_orm.id,
                        'user_id': file_orm.user_id,
                        'file_name': file_orm.file_name,
                        'file_url': file_orm.file_url,
                        'file_type': file_orm.file_type,
                        # Access to a document is decided by its workspace,
                        # not by who uploaded it, so callers that authorize a
                        # read need this here.
                        'workspace_id': file_orm.workspace_id,
                        'processing_status': file_orm.processing_status,
                        'created_at': file_orm.created_at.isoformat() if file_orm.created_at else None
                    }
                return None
            except Exception as e:
                logger.error(f"Error getting file by name {filename}: {e}", exc_info=True)
                return None

    async def update_file_type(self, file_id: int, file_type: str) -> bool:
        """Update the file_type of a file.

        Args:
            file_id: ID of the file to update
            file_type: New file type (pdf, youtube, etc.)

        Returns:
            bool: True if update was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()
                if not file_orm:
                    logger.warning(f"File {file_id} not found for type update")
                    return False
                file_orm.file_type = file_type
                await session.commit()
                logger.info(f"Updated file {file_id} type to {file_type}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating file {file_id} type: {e}", exc_info=True)
                return False

    async def update_file_status(self, file_id: int, status: str) -> bool:
        """Update the processing status of a file.

        Args:
            file_id: ID of the file to update
            status: New processing status

        Returns:
            bool: True if update was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()
                if not file_orm:
                    logger.warning(f"File {file_id} not found for status update")
                    return False
                file_orm.processing_status = status
                user_id = file_orm.user_id
                await session.commit()
                logger.info(f"Updated file {file_id} status to {status}")

                try:
                    await websocket_manager.send_message(
                        user_id=str(user_id),
                        event_type="file_status_update",
                        data={"file_id": int(file_id), "status": status},
                    )
                except Exception as ws_err:
                    logger.debug(f"WebSocket notify failed for file {file_id} status update: {ws_err}")

                api_base_url = os.getenv("API_BASE_URL")
                if api_base_url:
                    api_base_url = api_base_url.rstrip("/")
                    url = f"{api_base_url}/api/v1/internal/notify-client"
                    payload = {
                        "user_id": str(int(user_id)),
                        "event_type": "file_status_update",
                        "data": {"file_id": int(file_id), "status": status},
                    }

                    def _post():
                        try:
                            requests.post(url, json=payload, timeout=5)
                        except Exception:
                            return

                    try:
                        await asyncio.to_thread(_post)
                    except Exception:
                        pass

                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating file {file_id} status: {e}", exc_info=True)
                return False

    async def create_file(self, file_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new file record with enhanced error handling and uniqueness checks.

        Args:
            file_data: Dictionary containing file information

        Returns:
            Dict: Created file record with id and filename, or empty dict if failed
        """
        async with self.get_async_session() as session:
            try:
                logger.debug(f"Creating new file with data: {file_data}")

                # Validate required fields
                required_fields = ['user_id', 'filename', 'file_type', 'status']
                for field in required_fields:
                    if field not in file_data or file_data[field] is None:
                        raise ValueError(f"Missing required field: {field}")

                file_orm = FileORM(
                    user_id=file_data["user_id"],
                    file_name=file_data["filename"],
                    file_url=file_data.get("url", ""),
                    file_type=file_data["file_type"],
                    processing_status=file_data["status"]
                )

                session.add(file_orm)
                await session.flush()
                file_id = file_orm.id
                await session.commit()

                logger.info(f"Successfully created file {file_data['filename']} (ID: {file_id}) for user {file_data['user_id']}")
                return {"id": file_id, "filename": file_orm.file_name}

            except IntegrityError as e:
                await session.rollback()
                error_msg = f"Integrity constraint violation creating file {file_data.get('filename', 'unknown')}: {str(e)}"
                logger.error(error_msg, exc_info=True)

                # Check for specific constraint violations
                if "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
                    logger.warning(f"File with similar attributes already exists: {file_data}")
                    # Try to find existing file
                    try:
                        stmt = select(FileORM).where(
                            and_(
                                FileORM.user_id == file_data["user_id"],
                                FileORM.file_name == file_data["filename"]
                            )
                        )
                        result = await session.execute(stmt)
                        existing_file = result.scalar_one_or_none()
                        if existing_file:
                            return {"id": existing_file.id, "filename": existing_file.file_name}
                    except Exception as find_error:
                        logger.error(f"Error finding existing file: {find_error}")

                return {}

            except Exception as e:
                await session.rollback()
                error_msg = f"Error creating file {file_data.get('filename', 'unknown')}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                return {}