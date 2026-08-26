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
from ..models import PageRead as PageReadORM

# Import SQLAlchemy async components
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.exc import IntegrityError

import json
import os
import re
import requests

from api.core.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)


# Tokens that identify an answer rather than describe it: numbers, error codes,
# model designations, fractions.
#
# WHY THIS EXISTS, MEASURED 2026-08-14
#
# The text arm ranks with ts_rank_cd, which scores COVER DENSITY -- how tightly
# the query's words cluster in a chunk -- and has no inverse document frequency.
# It is not BM25, despite what the code called it. BM25's defining property is
# that a rare term counts for more than a common one, and Postgres full-text
# ranking does not do that at all.
#
# On the HVAC corpus, 1,528 chunks, for the question "liquid pressure is 251
# psig and subcooling is 10 degrees":
#
#     251            3 chunks      <- the discriminator
#     degrees        9
#     subcooling    29
#     psig          49
#     liquid       123
#     pressure     196
#     temperature  215
#
# All weighted equally, so eight chunks thick with "liquid pressure temperature"
# outranked the single chunk in the corpus containing 251, and NONE of those
# eight contained it. Every failing benchmark question had this shape: 4350 in 1
# chunk, 670 in 1, E4 in 2, F8 in 3, 335 in 3, 1600 in 5.
#
# Ranking by the rare token alone puts the right page first. So this arm exists
# to give those tokens a vote of their own, rather than trying to teach
# ts_rank_cd about rarity, which it has no mechanism for.
_LITERAL_TOKEN = re.compile(r"\b(?:\d+(?:[./]\d+)?|[A-Za-z]{1,2}\d{1,4})\b")

# Letter-digit pairs that are grid references or list markers rather than part
# names. Kept deliberately short: the pattern above requires a digit, so ordinary
# words like "in" and "at" can never reach here and listing them was noise.
_LITERAL_STOPWORDS = {"a1", "a2", "b1", "b2"}


def literal_tokens(query: str) -> List[str]:
    """The identifying tokens in a question, most specific first."""
    seen: List[str] = []
    for raw in _LITERAL_TOKEN.findall(query or ""):
        tok = raw.lower()
        if tok in _LITERAL_STOPWORDS or tok in seen:
            continue
        # A bare 0-10 is a quantity ("10 degrees"), not an identifier, and
        # matches a large share of a technical corpus.
        if tok.isdigit() and len(tok) <= 2 and int(tok) <= 10:
            continue
        seen.append(tok)
    return seen[:8]


def _serialize_file(file_orm) -> Dict[str, Any]:
    """One file row as a dict, for every caller that returns one.

    Extracted 2026-08-26. get_file_by_id and get_file_by_name held byte-identical
    copies of this, so adding the currency fields to one would have left the
    other answering an older shape of the same question.
    """
    return {
        'id': file_orm.id,
        'user_id': file_orm.user_id,
        'file_name': file_orm.file_name,
        'file_url': file_orm.file_url,
        'file_type': file_orm.file_type,
        # Access to a document is decided by its workspace, not by who uploaded
        # it, so callers that authorize a read need this here.
        'workspace_id': file_orm.workspace_id,
        'processing_status': file_orm.processing_status,
        'created_at': file_orm.created_at.isoformat() if file_orm.created_at else None,
        'effective_date': file_orm.effective_date.isoformat() if file_orm.effective_date else None,
        'superseded_by_id': file_orm.superseded_by_id,
    }


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

    async def stored_page_numbers(self, file_id: int) -> set:
        """Which pages of this file are already in the database.

        This is what makes a re-run resume instead of starting again. A batch
        is written in one transaction, so a page either has its segment or it
        has nothing: there is no half-written page to detect. That is why this
        can be answered from `segments` alone, with no progress column to keep
        in step with reality and no migration.
        """
        async with self.get_async_session() as session:
            rows = await session.execute(
                select(SegmentORM.page_number).where(SegmentORM.file_id == int(file_id))
            )
            return {r for (r,) in rows.all() if r is not None}

    async def cached_page_reads(self, file_id: int) -> Dict[int, Dict[str, Any]]:
        """Pages of this file that extraction has already produced.

        Read once at the start of extraction. A page that is here does not go
        to the vision model again, which is the whole point: that call costs
        about three minutes and real money, and until now an interrupted ingest
        threw away every one of them.
        """
        async with self.get_async_session() as session:
            rows = await session.execute(
                select(
                    PageReadORM.page_number,
                    PageReadORM.text,
                    PageReadORM.source,
                    PageReadORM.flags,
                ).where(PageReadORM.file_id == int(file_id))
            )
            return {
                int(n): {"text": t, "source": s, "flags": f}
                for (n, t, s, f) in rows.all()
                if n is not None
            }

    async def save_page_read(
        self,
        file_id: int,
        page_number: int,
        text_content: str,
        source: str,
        flags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Keep one extracted page, as soon as it exists.

        Called per page rather than per document on purpose. Saving at the end
        would keep exactly the runs that did not need saving.

        Never raises. This is a cache, and failing to write it must not fail an
        ingest that is otherwise going fine.
        """
        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            async with self.get_async_session() as session:
                stmt = (
                    pg_insert(PageReadORM.__table__)
                    .values(
                        file_id=int(file_id),
                        page_number=int(page_number),
                        text=text_content,
                        source=source,
                        flags=flags or None,
                    )
                    # A re-run that got further than a previous one should be
                    # able to overwrite a page rather than collide with it.
                    .on_conflict_do_update(
                        index_elements=["file_id", "page_number"],
                        set_={"text": text_content, "source": source, "flags": flags or None},
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.warning(
                f"Could not cache page {page_number} of file {file_id}: {type(e).__name__}: {e}"
            )

    async def embeddings_for_hashes(self, file_id: int, hashes: List[str]) -> Dict[str, Any]:
        """Vectors this organization has already paid to compute.

        Embedding is a pure function of its input, so the same text always has
        the same vector and computing it twice is money for nothing. The common
        cases are a customer re-uploading a corrected document and boilerplate
        that repeats across many files.

        **Scoped to the organization that owns `file_id`, on purpose.** Reusing
        another tenant's vector would leak nothing: the caller already holds the
        text, and the vector is derived from it with no other input. But a cache
        that crosses tenants is a thing somebody has to keep proving is safe
        every time this code is touched, and nearly all the saving is a customer
        re-uploading their own document. The narrow version buys most of the
        benefit and none of the argument.

        Returns {hash: embedding} for whatever was found, which may be empty.
        """
        if not hashes:
            return {}

        async with self.get_async_session() as session:
            rows = await session.execute(
                text(
                    """
                    WITH owner AS (
                      SELECT w.organization_id AS org
                      FROM files f
                      JOIN workspaces w ON w.id = f.workspace_id
                      WHERE f.id = :file_id
                    )
                    SELECT DISTINCT ON (c.content_hash) c.content_hash, c.embedding
                    FROM chunks c
                    JOIN files f ON f.id = c.file_id
                    JOIN workspaces w ON w.id = f.workspace_id
                    WHERE c.content_hash = ANY(:hashes)
                      AND c.embedding IS NOT NULL
                      AND w.organization_id = (SELECT org FROM owner)
                    """
                ),
                {"file_id": int(file_id), "hashes": list(hashes)},
            )
            found = {}
            for content_hash, embedding in rows.all():
                if embedding is None:
                    continue
                # pgvector hands this back as a string on a raw query.
                if isinstance(embedding, str):
                    embedding = [float(x) for x in embedding.strip("[]").split(",") if x]
                found[content_hash] = list(embedding)
            return found

    async def update_file_with_chunks(
        self,
        user_id: int,
        filename: str,
        file_type: str,
        extracted_data: List[Dict],
        file_id: Optional[int] = None,
        mark_processed: bool = True,
    ) -> bool:
        """Store processed file data with embeddings, segments, and metadata.

        Args:
            user_id: ID of the user who owns the file
            filename: Name of the file
            file_type: Type of file (pdf, video, etc.)
            extracted_data: Processed data containing chunks and embeddings
            mark_processed: Whether this call finishes the document. False when
                storing one batch of a larger file, because a 500-page PDF that
                says "processed" after its first fifty pages is worse than one
                that says nothing: the file list shows it ready and the rest
                never arrives visibly.

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
                        processing_status="processed" if mark_processed else "embedding",
                    )
                    session.add(file)
                    await session.flush()
                else:
                    file.file_type = file_type
                    if mark_processed:
                        file.processing_status = "processed"

                # What was already here before this call, so the check at the
                # end measures what *this* call stored. Comparing against the
                # file's total would pass trivially on every batch after the
                # first, which is exactly when a silent partial write would
                # start being possible.
                already_stored = (await session.execute(
                    select(func.count(ChunkORM.id)).where(ChunkORM.file_id == file.id)
                )).scalar() or 0

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
                                     'embedding', 'chunks', 'page_text')
                    }
                    segment = SegmentORM(
                        file_id=file.id,
                        content=page_text,
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
                            # What was embedded, so the next document holding
                            # this same text can reuse the vector instead of
                            # buying it again.
                            content_hash=u.get('content_hash'),
                        ))
                        expected += 1

                await session.commit()

                # Assert the rows actually landed. The original failure reported
                # success while writing nothing retrievable, so trust the
                # database rather than the absence of an exception.
                stored = (await session.execute(
                    select(func.count(ChunkORM.id)).where(ChunkORM.file_id == file.id)
                )).scalar() or 0
                if stored - already_stored < expected:
                    logger.error(
                        f"Stored {stored - already_stored} chunks for {filename} "
                        f"but expected {expected}"
                    )
                    return False

                logger.info(
                    f"Stored {stored - already_stored} chunks and segments for "
                    f"{filename} (ID: {file.id}), {stored} in total"
                )
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
                    FileORM.workspace_id,
                    FileORM.effective_date,
                    FileORM.superseded_by_id
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
                        "created_at": file.created_at.isoformat() if file.created_at else None,
                        "effective_date": file.effective_date.isoformat() if file.effective_date else None,
                        # Present so the list can mark a document as replaced.
                        # Retrieval already skips these; without it here a
                        # customer sees the document sitting in the list and
                        # cannot tell why it is never cited.
                        "superseded_by_id": file.superseded_by_id,
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
    # Equal to the vector arm, because when a question carries an identifying
    # token that token IS the answer's address: 4350 appears in one chunk of
    # 1,528, E4 in two. Measured 2026-08-14.
    DEFAULT_LITERAL_WEIGHT = float(os.getenv("LITERAL_WEIGHT", "0.7"))
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
        lw = self.DEFAULT_LITERAL_WEIGHT
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
                else:
                    # A document that has been replaced does not answer
                    # questions any more. One clause, applied to all three arms
                    # below, because they share where_sql: excluding it in the
                    # vector arm alone would let the keyword arm resurrect it.
                    #
                    # Only when the search is not already scoped to one file.
                    # Asking about a specific document is an explicit request
                    # for that document, superseded or not, and refusing to
                    # read a file the customer named would be a bug.
                    where_clauses.append("f.superseded_by_id IS NULL")

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
                        )::tsquery AS keywords,
                        -- An empty string is not a valid tsquery, so an
                        -- unmatchable placeholder stands in when a question has
                        -- no identifying tokens; :has_literals gates the arm.
                        CAST(:literals AS tsquery) AS literals
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
                    -- The identifying tokens, given a vote of their own.
                    -- ts_rank_cd has no IDF, so in the text arm "251" counts
                    -- exactly as much as "temperature"; here only the rare
                    -- tokens are searched, so a chunk containing them cannot
                    -- be outranked by one merely dense in ordinary words.
                    lit AS (
                      SELECT c.id AS chunk_id,
                             ROW_NUMBER() OVER (
                               ORDER BY ts_rank_cd(c.tsv, q.literals, 32) DESC
                             ) AS rank
                      FROM chunks c
                      JOIN files f ON f.id = c.file_id
                      CROSS JOIN query q
                      WHERE """ + where_sql + """ AND :has_literals
                        AND c.tsv @@ q.literals
                      ORDER BY ts_rank_cd(c.tsv, q.literals, 32) DESC
                      LIMIT :candidates
                    ),
                    -- Ranked on the chunk, like the other two arms. This used
                    -- to join `segments` and then rank `c.tsv` anyway, left
                    -- over from when the text arm read `s.tsv`. The join
                    -- decided nothing: every chunk has a segment, checked
                    -- 2026-08-15 across 2,633 rows, so it only cost work.
                    txt AS (
                      SELECT c.id AS chunk_id,
                             ROW_NUMBER() OVER (
                               ORDER BY ts_rank_cd(c.tsv, q.keywords, 32) DESC
                             ) AS rank
                      FROM chunks c
                      JOIN files f ON f.id = c.file_id
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
                        UNION ALL
                        SELECT chunk_id, rank,
                               CAST(:literal_weight AS double precision) AS weight FROM lit
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

                tokens = literal_tokens(query)
                params = {
                    "literals": " | ".join(tokens) if tokens else "zzzznomatchzzzz",
                    "has_literals": bool(tokens),
                    "literal_weight": lw,
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

    async def set_document_currency(
        self,
        file_id: int,
        *,
        effective_date=None,
        set_effective_date: bool = False,
        superseded_by_id: Optional[int] = None,
        set_superseded_by: bool = False,
    ) -> bool:
        """Record when a document became true, and what replaced it.

        The two `set_*` flags exist because None is a meaningful value here:
        clearing a supersede link is how a customer says "this document is
        current again", and it has to be distinguishable from "the caller did
        not mention that field".

        Returns False rather than raising when the file is gone. The route has
        already authorized both files by workspace; this does not re-check that,
        and must not be called from anywhere that has not.
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == int(file_id))
                file_orm = (await session.execute(stmt)).scalar_one_or_none()
                if not file_orm:
                    return False
                if set_effective_date:
                    file_orm.effective_date = effective_date
                if set_superseded_by:
                    file_orm.superseded_by_id = superseded_by_id
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error setting currency on file {file_id}: {e}", exc_info=True)
                return False

    async def supersede_chain_reaches(self, start_id: int, target_id: int) -> bool:
        """Does following "replaced by" from start_id ever arrive at target_id?

        A cycle here is not a curiosity, it is an outage for the documents in
        it: retrieval skips anything with a replacement, so A replaced-by B and
        B replaced-by A means neither can ever be cited again, and nothing in
        the product would say why. Callers refuse the link when this is True.

        Bounded, because a corrupt chain must not spin forever. A workspace with
        a legitimate revision history longer than this does not exist.
        """
        seen = set()
        current = int(start_id)
        async with self.get_async_session() as session:
            for _ in range(64):
                if current == int(target_id):
                    return True
                if current in seen:
                    return True
                seen.add(current)
                nxt = (
                    await session.execute(
                        select(FileORM.superseded_by_id).where(FileORM.id == current)
                    )
                ).scalar_one_or_none()
                if nxt is None:
                    return False
                current = int(nxt)
        # Ran out of hops without terminating, which is itself a broken chain.
        return True

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
                    return _serialize_file(file_orm)
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
                    return _serialize_file(file_orm)
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
            file_data: Dictionary containing file information. `workspace_id` is
                optional but should almost always be given: visibility follows
                the workspace, so a file created without one is reachable by
                nobody, including the owner of the organization it was uploaded
                into. It used to be dropped silently even when passed.

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
                    processing_status=file_data["status"],
                    # Was accepted in the dict and then dropped here, so a
                    # caller that passed it got a document belonging to no
                    # workspace: invisible to every person in the organization,
                    # since visibility follows the workspace and not the
                    # uploader. Silent, because the row is created successfully.
                    workspace_id=file_data.get("workspace_id"),
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