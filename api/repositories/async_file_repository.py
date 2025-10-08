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
from sqlalchemy import select, and_, or_, desc, func, text
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
                    file_url=file_url,
                    is_processed=False,
                    is_processing=False
                )
                session.add(file_orm)
                await session.flush()
                file_id = file_orm.id
                await session.commit()
                logger.info(f"Successfully added file {file_name} for user {user_id}")
                return file_id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding file {file_name}: {e}", exc_info=True)
                return None

    async def update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Update file with extracted chunks and metadata.

        Args:
            user_id: ID of the user
            filename: Name of the file
            file_type: Type of the file (pdf, youtube, etc.)
            extracted_data: List of extracted data chunks

        Returns:
            bool: True if update was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Find the file by name and user
                stmt = select(FileORM).where(
                    and_(FileORM.user_id == user_id, FileORM.file_name == filename)
                )
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()

                if not file_orm:
                    logger.error(f"File {filename} not found for user {user_id}")
                    return False

                # Update file metadata
                file_orm.is_processed = True
                file_orm.is_processing = False
                file_orm.file_type = file_type

                # Clear existing chunks for this file
                await session.execute(
                    ChunkORM.__table__.delete().where(ChunkORM.file_id == file_orm.id)
                )

                # Add new chunks
                for chunk_data in extracted_data:
                    chunk_orm = ChunkORM(
                        file_id=file_orm.id,
                        content=chunk_data.get('content', ''),
                        embedding=chunk_data.get('embedding', []),
                        metadata_=chunk_data.get('metadata', {}),
                        page_number=chunk_data.get('page_number', 0),
                        start_time=chunk_data.get('start_time', 0.0),
                        end_time=chunk_data.get('end_time', 0.0)
                    )
                    session.add(chunk_orm)

                await session.commit()
                logger.info(f"Successfully updated file {filename} with {len(extracted_data)} chunks")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating file {filename}: {e}", exc_info=True)
                return False

    async def _async_update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Internal async method for updating file with chunks."""
        return await self.update_file_with_chunks(user_id, filename, file_type, extracted_data)

    async def get_files_for_user(self, user_id: int, skip: int = 0, limit: int = 10) -> List[File]:
        """Get files for a specific user with pagination.

        Args:
            user_id: ID of the user
            skip: Number of files to skip
            limit: Maximum number of files to return

        Returns:
            List[File]: List of file domain objects
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.user_id == user_id).offset(skip).limit(limit)
                result = await session.execute(stmt)
                files_orm = result.scalars().all()

                files = []
                for file_orm in files_orm:
                    file = File(
                        id=file_orm.id,
                        user_id=file_orm.user_id,
                        file_name=file_orm.file_name,
                        file_url=file_orm.file_url,
                        is_processed=file_orm.is_processed,
                        is_processing=file_orm.is_processing,
                        file_type=file_orm.file_type,
                        created_at=file_orm.created_at,
                        updated_at=file_orm.updated_at
                    )
                    files.append(file)

                return files

            except Exception as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return []

    async def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file entry and all associated data.

        Args:
            user_id: ID of the user
            file_id: ID of the file to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if file exists and belongs to user
                stmt = select(FileORM).where(
                    and_(FileORM.id == file_id, FileORM.user_id == user_id)
                )
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()

                if not file_orm:
                    logger.error(f"File {file_id} not found for user {user_id}")
                    return False

                # Delete associated chunks first
                await session.execute(
                    ChunkORM.__table__.delete().where(ChunkORM.file_id == file_id)
                )

                # Delete the file
                await session.delete(file_orm)
                await session.commit()

                logger.info(f"Successfully deleted file {file_id} for user {user_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
                return False

    async def query_chunks_by_embedding(self, user_id: int, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Query chunks by embedding similarity.

        Args:
            user_id: ID of the user
            query_embedding: Query embedding vector
            top_k: Number of top results to return

        Returns:
            List[Dict[str, Any]]: List of similar chunks with metadata
        """
        async with self.get_async_session() as session:
            try:
                # Get chunks for user's files
                stmt = select(ChunkORM).join(FileORM).where(FileORM.user_id == user_id)
                result = await session.execute(stmt)
                chunks_orm = result.scalars().all()

                similarities = []
                for chunk in chunks_orm:
                    if chunk.embedding and len(chunk.embedding) > 0:
                        # Calculate cosine similarity
                        similarity = 1 - cosine(query_embedding, chunk.embedding)
                        similarities.append({
                            'chunk_id': chunk.id,
                            'content': chunk.content,
                            'similarity': similarity,
                            'file_id': chunk.file_id,
                            'page_number': chunk.page_number,
                            'start_time': chunk.start_time,
                            'end_time': chunk.end_time,
                            'metadata': chunk.metadata_
                        })

                # Sort by similarity and return top k
                similarities.sort(key=lambda x: x['similarity'], reverse=True)
                return similarities[:top_k]

            except Exception as e:
                logger.error(f"Error querying chunks by embedding: {e}", exc_info=True)
                return []

    async def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get segments for a specific page.

        Args:
            file_id: ID of the file
            page_number: Page number

        Returns:
            List[Dict[str, Any]]: List of segments for the page
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SegmentORM).where(
                    and_(SegmentORM.file_id == file_id, SegmentORM.page_number == page_number)
                )
                result = await session.execute(stmt)
                segments_orm = result.scalars().all()

                segments = []
                for segment in segments_orm:
                    segments.append({
                        'id': segment.id,
                        'file_id': segment.file_id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'metadata': segment.metadata_
                    })

                return segments

            except Exception as e:
                logger.error(f"Error getting segments for page {page_number}: {e}", exc_info=True)
                return []

    async def get_segments_for_time_range(self, file_id: int, start_seconds: float, end_seconds: float) -> List[Dict[str, Any]]:
        """Get segments for a specific time range.

        Args:
            file_id: ID of the file
            start_seconds: Start time in seconds
            end_seconds: End time in seconds

        Returns:
            List[Dict[str, Any]]: List of segments for the time range
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SegmentORM).where(
                    and_(
                        SegmentORM.file_id == file_id,
                        SegmentORM.start_time >= start_seconds,
                        SegmentORM.end_time <= end_seconds
                    )
                )
                result = await session.execute(stmt)
                segments_orm = result.scalars().all()

                segments = []
                for segment in segments_orm:
                    segments.append({
                        'id': segment.id,
                        'file_id': segment.file_id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'metadata': segment.metadata_
                    })

                return segments

            except Exception as e:
                logger.error(f"Error getting segments for time range {start_seconds}-{end_seconds}: {e}", exc_info=True)
                return []

    async def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get file by ID.

        Args:
            file_id: ID of the file

        Returns:
            Optional[Dict[str, Any]]: File data or None if not found
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(FileORM.id == file_id)
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()

                if not file_orm:
                    return None

                return {
                    'id': file_orm.id,
                    'user_id': file_orm.user_id,
                    'file_name': file_orm.file_name,
                    'file_url': file_orm.file_url,
                    'is_processed': file_orm.is_processed,
                    'is_processing': file_orm.is_processing,
                    'file_type': file_orm.file_type,
                    'created_at': file_orm.created_at,
                    'updated_at': file_orm.updated_at
                }

            except Exception as e:
                logger.error(f"Error getting file {file_id}: {e}", exc_info=True)
                return None

    async def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get file by name for a specific user.

        Args:
            user_id: ID of the user
            filename: Name of the file

        Returns:
            Optional[Dict[str, Any]]: File data or None if not found
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(
                    and_(FileORM.user_id == user_id, FileORM.file_name == filename)
                )
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()

                if not file_orm:
                    return None

                return {
                    'id': file_orm.id,
                    'user_id': file_orm.user_id,
                    'file_name': file_orm.file_name,
                    'file_url': file_orm.file_url,
                    'is_processed': file_orm.is_processed,
                    'is_processing': file_orm.is_processing,
                    'file_type': file_orm.file_type,
                    'created_at': file_orm.created_at,
                    'updated_at': file_orm.updated_at
                }

            except Exception as e:
                logger.error(f"Error getting file {filename} for user {user_id}: {e}", exc_info=True)
                return None

    async def check_user_file_ownership(self, file_id: int, user_id: int) -> bool:
        """Check if a user owns a specific file.

        Args:
            file_id: ID of the file
            user_id: ID of the user

        Returns:
            bool: True if user owns the file, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(FileORM).where(
                    and_(FileORM.id == file_id, FileORM.user_id == user_id)
                )
                result = await session.execute(stmt)
                file_orm = result.scalar_one_or_none()

                return file_orm is not None

            except Exception as e:
                logger.error(f"Error checking ownership of file {file_id} for user {user_id}: {e}", exc_info=True)
                return False
