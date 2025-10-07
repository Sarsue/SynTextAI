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

logger = logging.getLogger(__name__)


class AsyncFileRepository(AsyncBaseRepository):
    """Async repository for file-related database operations."""

    async def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file to the database.

        Args:
            user_id: ID of the user who owns this file
            file_name: Name of the file
            file_url: URL where the file is stored

        Returns:
            int: The ID of the newly created file, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_file = FileORM(
                    user_id=user_id,
                    file_name=file_name,
                    file_url=file_url
                )
                session.add(new_file)
                await session.flush()  # Flush to get the ID without committing
                await session.refresh(new_file)
                logger.info(f"Added new file {file_name} (ID: {new_file.id}) for user {user_id}")
                return new_file.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding file: {e}", exc_info=True)
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
            # Use run_in_executor to make the synchronous database operation non-blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    self._async_update_file_with_chunks,
                    user_id, filename, file_type, extracted_data
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for update_file_with_chunks: {e}", exc_info=True)
            return False

    async def _async_update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Async implementation of update_file_with_chunks."""
        async with self.get_async_session() as session:
            try:
                # Get or create the file record
                file = await session.query(FileORM).filter(
                    FileORM.user_id == user_id,
                    FileORM.file_name == filename
                ).first()

                if not file:
                    file = FileORM(
                        user_id=user_id,
                        file_name=filename,
                        file_url=""  # URL might be added later
                    )
                    session.add(file)
                    await session.flush()  # To get the file ID

                file_id = file.id

                # Process chunks
                for chunk_data in extracted_data:
                    chunk_content = chunk_data.get('content', '')
                    chunk_embedding = chunk_data.get('embedding', [])
                    chunk_metadata = chunk_data.get('metadata', {})

                    # Create chunk
                    new_chunk = ChunkORM(
                        file_id=file_id,
                        content=chunk_content,
                        embedding=chunk_embedding,
                        metadata=chunk_metadata
                    )
                    session.add(new_chunk)

                    # Process segments for this chunk
                    segments = chunk_data.get('segments', [])
                    for segment in segments:
                        segment_content = segment.get('content', '')
                        segment_metadata = segment.get('metadata', {})

                        new_segment = SegmentORM(
                            file_id=file_id,
                            chunk_id=new_chunk.id,
                            content=segment_content,
                            metadata=segment_metadata
                        )
                        session.add(new_segment)

                # Update file status
                file.status = 'processed'
                await session.commit()

                logger.info(f"Successfully updated file {filename} with chunks for user {user_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating file with chunks: {e}", exc_info=True)
                return False

    async def get_files_for_user(self, user_id: int, skip: int = 0, limit: int = 10) -> List[File]:
        """Get files for a user with pagination.

        Args:
            user_id: ID of the user
            skip: Number of files to skip
            limit: Maximum number of files to return

        Returns:
            List[File]: List of file domain objects
        """
        async with self.get_async_session() as session:
            try:
                files_orm = await session.query(FileORM).filter(
                    FileORM.user_id == user_id
                ).offset(skip).limit(limit).all()

                files = []
                for file_orm in files_orm:
                    file_obj = File(
                        id=file_orm.id,
                        user_id=file_orm.user_id,
                        file_name=file_orm.file_name,
                        file_url=file_orm.file_url,
                        file_type=file_orm.file_type,
                        status=file_orm.status,
                        created_at=file_orm.created_at,
                        updated_at=file_orm.updated_at
                    )
                    files.append(file_obj)

                return files
            except Exception as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return []

    async def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data.

        Args:
            user_id: ID of the user
            file_id: ID of the file to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if user owns the file
                file = await session.query(FileORM).filter(
                    FileORM.id == file_id,
                    FileORM.user_id == user_id
                ).first()

                if not file:
                    logger.warning(f"User {user_id} attempted to delete unauthorized file {file_id}")
                    return False

                # Delete the file (cascade will delete chunks and segments)
                await session.delete(file)
                await session.commit()

                logger.info(f"Deleted file {file_id} for user {user_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
                return False

    async def query_chunks_by_embedding(self, user_id: int, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Query chunks by embedding similarity.

        Args:
            user_id: ID of the user
            query_embedding: Embedding vector to search for
            top_k: Number of top results to return

        Returns:
            List[Dict]: List of similar chunks with scores
        """
        async with self.get_async_session() as session:
            try:
                # Get all chunks for this user
                chunks = await session.query(ChunkORM).join(FileORM).filter(
                    FileORM.user_id == user_id
                ).all()

                # Calculate similarities
                similarities = []
                query_embedding_np = np.array(query_embedding)

                for chunk in chunks:
                    if chunk.embedding:
                        chunk_embedding_np = np.array(chunk.embedding)
                        # Use cosine similarity
                        similarity = 1 - cosine(query_embedding_np, chunk_embedding_np)
                        similarities.append({
                            'chunk': chunk,
                            'similarity': similarity
                        })

                # Sort by similarity and get top k
                similarities.sort(key=lambda x: x['similarity'], reverse=True)
                top_chunks = similarities[:top_k]

                result = []
                for item in top_chunks:
                    chunk = item['chunk']
                    result.append({
                        'id': chunk.id,
                        'content': chunk.content,
                        'similarity': item['similarity'],
                        'file_id': chunk.file_id
                    })

                return result

            except Exception as e:
                logger.error(f"Error querying chunks by embedding: {e}", exc_info=True)
                return []

    async def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file.

        Args:
            file_id: ID of the file
            page_number: Page number to get segments for

        Returns:
            List[Dict]: List of segments for the page
        """
        async with self.get_async_session() as session:
            try:
                segments = await session.query(SegmentORM).filter(
                    SegmentORM.file_id == file_id,
                    SegmentORM.metadata['page_number'].as_integer() == page_number
                ).all()

                result = []
                for segment in segments:
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'metadata': segment.metadata
                    })

                return result
            except Exception as e:
                logger.error(f"Error getting segments for page {page_number}: {e}", exc_info=True)
                return []

    async def get_segments_for_time_range(self, file_id: int, start_seconds: float, end_seconds: float) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific time range in a video.

        Args:
            file_id: ID of the file
            start_seconds: Start time in seconds
            end_seconds: End time in seconds

        Returns:
            List[Dict]: List of segments for the time range
        """
        async with self.get_async_session() as session:
            try:
                segments = await session.query(SegmentORM).filter(
                    SegmentORM.file_id == file_id,
                    SegmentORM.metadata['start_seconds'].as_float() >= start_seconds,
                    SegmentORM.metadata['end_seconds'].as_float() <= end_seconds
                ).all()

                result = []
                for segment in segments:
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'metadata': segment.metadata
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
            Optional[Dict]: File data if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                file = await session.get(FileORM, file_id)
                if not file:
                    return None

                return {
                    'id': file.id,
                    'user_id': file.user_id,
                    'file_name': file.file_name,
                    'file_url': file.file_url,
                    'file_type': file.file_type,
                    'status': file.status,
                    'created_at': file.created_at,
                    'updated_at': file.updated_at
                }
            except Exception as e:
                logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
                return None

    async def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename.

        Args:
            user_id: ID of the user
            filename: Name of the file

        Returns:
            Optional[Dict]: File data if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                file = await session.query(FileORM).filter(
                    FileORM.user_id == user_id,
                    FileORM.file_name == filename
                ).first()

                if not file:
                    return None

                return {
                    'id': file.id,
                    'user_id': file.user_id,
                    'file_name': file.file_name,
                    'file_url': file.file_url,
                    'file_type': file.file_type,
                    'status': file.status,
                    'created_at': file.created_at,
                    'updated_at': file.updated_at
                }
            except Exception as e:
                logger.error(f"Error getting file by name {filename}: {e}", exc_info=True)
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
                file = await session.query(FileORM).filter(
                    FileORM.id == file_id,
                    FileORM.user_id == user_id
                ).first()

                return file is not None
            except Exception as e:
                logger.error(f"Error checking file ownership: {e}", exc_info=True)
                return False
