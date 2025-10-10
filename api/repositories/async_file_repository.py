"""
Async File repository for managing file-related database operations.

This module mirrors the sync FileRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Dict, Any, Tuple
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy.spatial.distance import cosine, euclidean

from .async_base_repository import AsyncBaseRepository
from .domain_models import File, Segment, Chunk

# Import ORM models from the new models module
from ..models import File as FileORM, Chunk as ChunkORM, KeyConcept as KeyConceptORM
from ..models import Segment as SegmentORM

# Import SQLAlchemy async components
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

class AsyncFileRepository(AsyncBaseRepository):
    """Async repository for file operations."""

    async def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file to the database.

        Args:
            user_id: ID of the user who owns the file
            file_name: Name of the file
            file_url: URL where the file is stored

        Returns:
            Optional[int]: The ID of the newly created file, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                file_orm = FileORM(
                    user_id=user_id,
                    file_name=file_name,
                    file_url=file_url
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

    async def update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Store processed file data with embeddings, segments, and metadata.

        Args:
            user_id: ID of the user who owns the file
            filename: Name of the file
            file_type: Type of file (pdf, video, etc.)
            extracted_data: Processed data containing chunks and embeddings

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    self._sync_update_file_with_chunks,
                    user_id, filename, file_type, extracted_data
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for update_file_with_chunks: {e}", exc_info=True)
            return False

    def _sync_update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Synchronous implementation of update_file_with_chunks."""
        with self.get_unit_of_work() as uow:
            try:
                # Get or create the file record
                file = uow.session.query(FileORM).filter(
                    FileORM.user_id == user_id,
                    FileORM.file_name == filename
                ).first()

                if not file:
                    file = FileORM(
                        user_id=user_id,
                        file_name=filename,
                        file_url="",  # URL might be added later
                        file_type=file_type
                    )
                    uow.session.add(file)
                    uow.session.flush()

                # Update file metadata
                file.file_type = file_type
                file.processing_status = 'processed'

                # Process segments and chunks
                for segment_data in extracted_data:
                    segment = SegmentORM(
                        file_id=file.id,
                        content=segment_data.get('content', ''),
                        page_number=segment_data.get('page_number')
                    )

                    # Handle metadata
                    meta_data = {}
                    for key in segment_data:
                        if key not in ['content', 'page_number', 'chunks']:
                            meta_data[key] = segment_data[key]

                    if meta_data:
                        segment.meta_data = meta_data

                    uow.session.add(segment)
                    uow.session.flush()

                    # Process chunks within this segment
                    if 'chunks' in segment_data:
                        for chunk_data in segment_data['chunks']:
                            chunk = ChunkORM(
                                segment_id=segment.id,
                                content=chunk_data.get('content', ''),
                                embedding=chunk_data.get('embedding')
                            )
                            uow.session.add(chunk)

                return True
            except IntegrityError as e:
                logger.error(f"Integrity error updating file with chunks: {e}", exc_info=True)
                return False
            except Exception as e:
                logger.error(f"Error updating file with chunks: {e}", exc_info=True)
                return False

    async def get_files_for_user(self, user_id: int, skip: int = 0, limit: int = 10) -> Dict[str, Any]:
        """Get paginated files for a user.

        Args:
            user_id: ID of the user
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return (for pagination)

        Returns:
            Dict: {
                'items': List[Dict],  # List of file records with metadata
                'total': int,         # Total number of files for the user
                'page': int,          # Current page number (1-based)
                'page_size': int      # Number of items per page
            }
        """
        async with self.get_async_session() as session:
            try:
                # Get total count
                stmt = select(func.count(FileORM.id)).where(FileORM.user_id == user_id)
                result = await session.execute(stmt)
                total = result.scalar() or 0

                # Get paginated results
                stmt = select(
                    FileORM.id,
                    FileORM.file_name,
                    FileORM.file_url,
                    FileORM.created_at,
                    FileORM.processing_status
                ).where(FileORM.user_id == user_id).order_by(FileORM.created_at.desc()).offset(skip).limit(limit)
                result = await session.execute(stmt)
                files = result.fetchall()

                items = []
                for file in files:
                    file_dict = {
                        "id": file.id,
                        "file_name": file.file_name,
                        "name": file.file_name,
                        "file_url": file.file_url,
                        "publicUrl": file.file_url,
                        "processing_status": file.processing_status,
                        "created_at": file.created_at.isoformat() if file.created_at else None,
                    }
                    items.append(file_dict)

                return {
                    'items': items,
                    'total': total,
                    'page': (skip // limit) + 1,
                    'page_size': limit
                }

            except Exception as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return {'items': [], 'total': 0, 'page': 1, 'page_size': limit}

    async def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data.

        Args:
            user_id: ID of the user who owns the file
            file_id: ID of the file to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if the file exists and belongs to the user
                stmt = select(FileORM).where(and_(FileORM.id == file_id, FileORM.user_id == user_id))
                result = await session.execute(stmt)
                file_obj = result.scalar_one_or_none()

                if not file_obj:
                    logger.warning(f"File {file_id} not found or not owned by user {user_id}")
                    return False

                # Delete the file (cascade should handle related entities)
                await session.delete(file_obj)
                await session.commit()
                logger.info(f"Successfully deleted file {file_id} for user {user_id} with cascade")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)

                # Fallback: Try SQL deletion using the same session
                try:
                    # Delete associated entities
                    await session.execute(text(f"DELETE FROM flashcards WHERE file_id = {file_id}"))
                    await session.execute(text(f"DELETE FROM quiz_questions WHERE file_id = {file_id}"))
                    await session.execute(text(f"DELETE FROM key_concepts WHERE file_id = {file_id}"))
                    await session.execute(text(f"DELETE FROM chunks WHERE file_id = {file_id}"))
                    await session.execute(text(f"DELETE FROM segments WHERE file_id = {file_id}"))
                    await session.execute(text(f"DELETE FROM files WHERE id = {file_id} AND user_id = {user_id}"))
                    await session.commit()
                    logger.info(f"Successfully deleted file {file_id} for user {user_id} using manual SQL deletion")
                    return True
                except Exception as sql_error:
                    await session.rollback()
                    logger.error(f"SQL fallback error deleting file {file_id}: {sql_error}", exc_info=True)
                    return False

    async def query_chunks_by_embedding(
        self,
        user_id: int, 
        query_embedding: List[float], 
        top_k: int = 5, 
        similarity_type: str = 'l2'
    ) -> List[Dict]:
        """Retrieves chunks with the highest similarity to the query embedding.

        Args:
            user_id: ID of the user
            query_embedding: Embedding of the user's query
            top_k: Number of top results to return
            similarity_type: Type of similarity calculation ('l2', 'cosine')

        Returns:
            List[Dict]: List of chunks with similarity scores
        """
        async with self.get_async_session() as session:
            try:
                # Get all files for the user
                stmt = select(FileORM).where(FileORM.user_id == user_id)
                result = await session.execute(stmt)
                files = result.scalars().all()

                if not files:
                    return []

                file_ids = [file.id for file in files]

                # Get all chunks with embeddings
                stmt = select(ChunkORM).where(
                    and_(ChunkORM.file_id.in_(file_ids), ChunkORM.embedding != None)
                )
                result = await session.execute(stmt)
                chunks = result.scalars().all()

                if not chunks:
                    return []

                # Calculate similarity scores
                results = []
                query_embedding_np = np.array(query_embedding)

                for chunk in chunks:
                    chunk_embedding = np.array(chunk.embedding)

                    if similarity_type.lower() == 'cosine':
                        similarity = 1 - cosine(query_embedding_np, chunk_embedding)
                    else:
                        # Default to L2 (euclidean) distance
                        distance = euclidean(query_embedding_np, chunk_embedding)
                        similarity = 1 / (1 + distance)  # Transform distance to similarity [0,1]

                    results.append({
                        'chunk_id': chunk.id,
                        'file_id': chunk.file_id,
                        'content': chunk.content,
                        'similarity': float(similarity)
                    })

                # Sort by similarity and get top_k results
                results.sort(key=lambda x: x['similarity'], reverse=True)
                return results[:top_k]

            except Exception as e:
                logger.error(f"Error querying chunks by embedding: {e}", exc_info=True)
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
                # Build base query
                stmt = select(SegmentORM).where(SegmentORM.file_id == file_id)

                # Apply filter for time range
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
                        'file_name': file_orm.file_name,
                        'file_url': file_orm.file_url,
                        'created_at': file_orm.created_at.isoformat() if file_orm.created_at else None,
                        'user_id': file_orm.user_id,
                        'file_type': file_orm.file_type,
                        'processing_status': file_orm.processing_status
                    }
                return None
            except Exception as e:
                logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
                return None

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
                file = result.scalar_one_or_none()

                if not file:
                    return None

                return {
                    'id': file.id,
                    'user_id': file.user_id,
                    'file_name': file.file_name,
                    'file_url': file.file_url,
                    'created_at': file.created_at.isoformat() if file.created_at else None
                }
            except Exception as e:
                logger.error(f"Error getting file by name {filename}: {e}", exc_info=True)
                return None

    async def check_user_file_ownership(self, file_id: int, user_id: int) -> bool:
        """Check if a user owns a specific file."""
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM.id).where(and_(FileORM.id == file_id, FileORM.user_id == user_id))
                result = await session.execute(stmt)
                exists = result.scalar_one_or_none() is not None
                return exists
            except Exception as e:
                logger.error(f"Error checking file ownership for file {file_id}, user {user_id}: {e}", exc_info=True)
                return False