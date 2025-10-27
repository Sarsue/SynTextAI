"""
Async File repository for managing file-related database operations.
"""
from typing import Optional, List, Dict, Any
import logging
import asyncio
import numpy as np
from scipy.spatial.distance import cosine, euclidean

from .async_base_repository import AsyncBaseRepository
from ..models import File as FileORM, Chunk as ChunkORM, KeyConcept as KeyConceptORM
from ..models import Segment as SegmentORM

# Import SQLAlchemy async components
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

class AsyncFileRepository(AsyncBaseRepository):
    """Async repository for file operations."""

    def __init__(self, database_url: str = None):
        """Initialize the async file repository.

        Args:
            database_url: Database connection URL. If None, uses environment variable.
        """
        super().__init__(database_url)

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
                file_orm = FileORM(
                    user_id=user_id,
                    file_name=file_name,
                    file_url=file_url,
                    processing_status="uploaded"  # Explicitly set status to ensure it's not None
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
        async with self.get_async_session() as session:
            try:
                # Get or create the file record
                stmt = select(FileORM).where(
                    and_(FileORM.user_id == user_id, FileORM.file_name == filename)
                )
                result = await session.execute(stmt)
                file = result.scalar_one_or_none()

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

                # Process segments and chunks
                for segment_data in extracted_data:
                    segment = SegmentORM(
                        file_id=file.id,
                        content=segment_data.get('content', ''),
                        page_number=segment_data.get('page_number')
                    )
                    meta_data = {k: v for k, v in segment_data.items() if k not in ['content', 'page_number', 'chunks']}
                    if meta_data:
                        segment.meta_data = meta_data
                    session.add(segment)
                    await session.flush()

                    if 'chunks' in segment_data:
                        for chunk_data in segment_data['chunks']:
                            chunk = ChunkORM(
                                segment_id=segment.id,
                                content=chunk_data.get('content', ''),
                                embedding=chunk_data.get('embedding')
                            )
                            session.add(chunk)

                await session.commit()
                logger.info(f"Updated file {filename} (ID: {file.id}) with chunks")
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
                    FileORM.processing_status,
                    FileORM.file_type
                ).where(FileORM.user_id == user_id).order_by(FileORM.created_at.desc()).offset(skip).limit(limit)
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
                error_msg = f"Error deleting file {file_id}: {str(e)[:1000]}"
                logger.error(error_msg, exc_info=True)
                try:
                    await session.execute(text("DELETE FROM flashcards WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM quiz_questions WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM key_concepts WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM chunks WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM segments WHERE file_id = :file_id"), {"file_id": file_id})
                    await session.execute(text("DELETE FROM files WHERE id = :file_id AND user_id = :user_id"),
                                         {"file_id": file_id, "user_id": user_id})
                    await session.commit()
                    logger.info(f"Successfully deleted file {file_id} for user {user_id} using manual SQL deletion")
                    return True
                except Exception as sql_error:
                    error_msg = f"SQL fallback error deleting file {file_id}: {str(sql_error)[:1000]}"
                    logger.error(error_msg, exc_info=True)
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

                # Get chunks with embeddings, limited to improve performance
                stmt = select(ChunkORM).where(
                    and_(ChunkORM.file_id.in_(file_ids), ChunkORM.embedding != None)
                ).limit(1000)
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
                        distance = euclidean(query_embedding_np, chunk_embedding)
                        similarity = 1 / (1 + distance)

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
                        'processing_status': file_orm.processing_status,
                        'created_at': file_orm.created_at.isoformat() if file_orm.created_at else None
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
                file_orm = result.scalar_one_or_none()
                if file_orm:
                    return {
                        'id': file_orm.id,
                        'user_id': file_orm.user_id,
                        'file_name': file_orm.file_name,
                        'file_url': file_orm.file_url,
                        'file_type': file_orm.file_type,
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
                await session.commit()
                logger.info(f"Updated file {file_id} status to {status}")
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