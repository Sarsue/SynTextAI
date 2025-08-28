"""
Asynchronous file repository implementation using SQLAlchemy async.
"""
from typing import List, Dict, Optional, Any, Type, Union
from datetime import datetime
import logging
from sqlalchemy import select, update, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from ..models.orm_models import File as FileModel, Chunk, Segment, KeyConcept
from ..models.file import File, FileCreate, FileUpdate
from .async_base_repository import AsyncBaseRepository
from .repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

class AsyncFileRepository(AsyncBaseRepository[File, FileCreate, FileUpdate]):
    """Asynchronous repository for file operations."""
    
    def __init__(self, repository_manager: RepositoryManager):
        """
        Initialize with repository manager.
        
        Args:
            repository_manager: RepositoryManager instance for session management
        """
        super().__init__(FileModel, repository_manager)
        self._repository_manager = repository_manager

    async def get_file_by_id(self, file_id: int) -> Optional[File]:
        """Get a file by its ID."""
        return await self.get(file_id)

    async def list_user_files(self, user_id: int, skip: int = 0, limit: int = 100) -> List[File]:
        """List all files for a user with pagination."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FileModel)
                .where(FileModel.user_id == user_id)
                .offset(skip)
                .limit(limit)
            )
            return result.scalars().all()

    async def get_file_by_name(self, user_id: int, filename: str) -> Optional[File]:
        """Get a file by user ID and filename."""
        async with self.session_scope as session:
            result = await session.execute(
                select(FileModel)
                .where(FileModel.user_id == user_id, FileModel.file_name == filename)
            )
            return result.scalars().first()

    async def create_file(self, file_data: Union[FileCreate, Dict[str, Any]], **kwargs) -> Optional[Union[File, int]]:
        """
        Create a new file record.
        
        Args:
            file_data: Either a FileCreate model or dict with file data
            **kwargs: Additional parameters:
                - return_id: If True, returns just the file ID (default: False)
                
        Returns:
            File: The created file object, or file ID if return_id=True
            None: If creation failed
        """
        try:
            # Handle both FileCreate and dict input
            if isinstance(file_data, dict):
                if 'processing_status' not in file_data:
                    file_data['processing_status'] = 'uploaded'
                file_data = FileCreate(**file_data)
                
            file = await self.create(obj_in=file_data)
            return file.id if kwargs.get('return_id') else file
            
        except Exception as e:
            logger.error(f"Error creating file: {e}", exc_info=True)
            return None

    async def update_file_with_chunks(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict[str, Any]]
    ) -> bool:
        """
        Store processed file data with embeddings, segments, and metadata asynchronously.
        
        Args:
            user_id: ID of the user who owns the file
            filename: Name of the file
            file_type: Type of the file (pdf, youtube, etc.)
            extracted_data: List of segments with chunks and embeddings
            
        Returns:
            bool: True if successful, False otherwise
        """
        from ..models.file import FileUpdate  # local import to avoid circulars in some setups
        
        async with self.session_scope as session:
            try:
                # Get or create the file record
                result = await session.execute(
                    select(FileModel)
                    .where(FileModel.user_id == user_id, FileModel.file_name == filename)
                )
                file = result.scalars().first()
                
                if not file:
                    # Create new file record
                    file = FileModel(
                        user_id=user_id,
                        file_name=filename,
                        file_type=file_type,
                        metadata={}
                    )
                    session.add(file)
                    await session.flush()
                
                # Delete existing segments and chunks for this file
                await session.execute(delete(Chunk).where(Chunk.file_id == file.id))
                await session.execute(delete(Segment).where(Segment.file_id == file.id))
                
                # Add new segments and chunks
                for segment_data in extracted_data:
                    segment = Segment(
                        file_id=file.id,
                        content=segment_data.get('content', ''),
                        page_number=segment_data.get('page_number'),
                        start_time=segment_data.get('start_time'),
                        end_time=segment_data.get('end_time'),
                        metadata=segment_data.get('metadata', {})
                    )
                    session.add(segment)
                    await session.flush()  # Get the segment ID for chunks
                    
                    for chunk_data in segment_data.get('chunks', []):
                        chunk = Chunk(
                            segment_id=segment.id,
                            file_id=file.id,
                            content=chunk_data.get('content', ''),
                            embedding=chunk_data.get('embedding'),
                            token_count=chunk_data.get('token_count', 0),
                            metadata=chunk_data.get('metadata', {})
                        )
                        session.add(chunk)
                
                # Update file status to completed
                update_data = {
                    'processing_status': 'completed'
                }
                
                # Use direct update since we already have a session
                await session.execute(
                    update(FileModel)
                    .where(FileModel.id == file.id)
                    .values(**update_data)
                )
                
                await session.commit()
                return True
                
            except Exception as e:
                logger.error(f"Error in update_file_with_chunks: {e}", exc_info=True)
                try:
                    await session.rollback()
                except Exception:
                    pass
                return False

    async def check_user_file_ownership(self, file_id: int, user_id: int) -> bool:
        """
        Check if a user owns a specific file.
        
        Args:
            file_id: ID of the file to check
            user_id: ID of the user to verify ownership for
            
        Returns:
            bool: True if the user owns the file, False otherwise
        """
        async with self.session_scope as session:
            result = await session.execute(
                select(FileModel)
                .where(FileModel.id == file_id, FileModel.user_id == user_id)
                .limit(1)
            )
            return result.scalars().first() is not None
            
    async def query_chunks_by_embedding(
        self,
        user_id: int,
        query_embedding: List[float],
        top_k: int = 5,
        similarity_type: str = 'cosine'
    ) -> List[Dict[str, Any]]:
        """
        Query chunks by embedding similarity.
        
        Args:
            user_id: ID of the user making the query
            query_embedding: The embedding vector to compare against
            top_k: Number of results to return
            similarity_type: Type of similarity to use ('cosine' or 'l2')
            
        Returns:
            List of matching chunks with similarity scores and metadata
        """
        from sqlalchemy import text
        
        try:
            async with self.session_scope as session:
                # Using SQLAlchemy's text() for raw SQL with parameters
                if similarity_type.lower() == 'cosine':
                    similarity_sql = """
                        SELECT 
                            c.id, 
                            c.content,
                            c.embedding <=> :query_embedding AS similarity,
                            f.id AS file_id,
                            f.file_name,
                            s.page_number,
                            s.start_time,
                            s.end_time
                        FROM chunks c
                        JOIN segments s ON c.segment_id = s.id
                        JOIN files f ON c.file_id = f.id
                        WHERE f.user_id = :user_id
                        ORDER BY c.embedding <=> :query_embedding
                        LIMIT :limit
                    """
                else:  # L2 distance
                    similarity_sql = """
                        SELECT 
                            c.id, 
                            c.content,
                            c.embedding <-> :query_embedding AS distance,
                            f.id AS file_id,
                            f.file_name,
                            s.page_number,
                            s.start_time,
                            s.end_time
                        FROM chunks c
                        JOIN segments s ON c.segment_id = s.id
                        JOIN files f ON c.file_id = f.id
                        WHERE f.user_id = :user_id
                        ORDER BY c.embedding <-> :query_embedding
                        LIMIT :limit
                    """
                
                result = await session.execute(
                    text(similarity_sql),
                    {
                        'query_embedding': query_embedding,
                        'user_id': user_id,
                        'limit': top_k
                    },
                )
                
                return [dict(row) for row in result.mappings()]
                
        except Exception as e:
            logger.error(f"Error in query_chunks_by_embedding: {e}", exc_info=True)
            return []

    async def search_files(self, user_id: int, query: str, limit: int = 10) -> List[File]:
        """Search files by filename or content."""
        try:
            async with self.session_scope as session:
                result = await session.execute(
                    select(FileModel)
                    .outerjoin(Segment, FileModel.id == Segment.file_id)
                    .where(
                        FileModel.user_id == user_id,
                        or_(
                            FileModel.file_name.ilike(f"%{query}%"),
                            Segment.content.ilike(f"%{query}%")
                        )
                    )
                    .distinct()
                )
                segments = result.scalars().all()
                
                return [
                    {
                        'id': segment.id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'chunks': [
                            {
                                'id': chunk.id,
                                'content': chunk.content,
                                'token_count': chunk.token_count,
                                'metadata': chunk.metadata or {}
                            }
                            for chunk in segment.chunks
                        ],
                        'metadata': segment.metadata or {}
                    }
                    for segment in segments
                ]
                
        except Exception as e:
            logger.error(f"Error in search_files: {e}", exc_info=True)
            return []

    async def get_segments_for_time_range(
        self, 
        file_id: int, 
        start_time: float, 
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Get segment contents for a specific time range of a video file.
        
        Args:
            file_id: ID of the file
            start_time: Start time in seconds
            end_time: Optional end time in seconds. If not provided, gets all segments after start_time
            
        Returns:
            List of segment dictionaries with their chunks
        """
        try:
            async with self.session_scope as session:
                query = (
                    select(Segment)
                    .options(selectinload(Segment.chunks))
                    .where(Segment.file_id == file_id)
                    .where(Segment.start_time >= start_time)
                )
                
                if end_time is not None:
                    query = query.where(Segment.end_time <= end_time)
                    
                result = await session.execute(query)
                segments = result.scalars().all()
                
                return [
                    {
                        'id': segment.id,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'text': getattr(segment, 'text', ''),
                        'content': segment.content,
                        'chunks': [
                            {
                                'id': chunk.id,
                                'text': getattr(chunk, 'text', ''),
                                'embedding': getattr(chunk, 'embedding', None),
                                'content': chunk.content,
                                'token_count': getattr(chunk, 'token_count', None),
                                'metadata': getattr(chunk, 'metadata', {}) or {}
                            }
                            for chunk in segment.chunks
                        ],
                        'metadata': getattr(segment, 'metadata', {}) or {}
                    }
                    for segment in segments
                ]
        except Exception as e:
            logger.error(f"Error in get_segments_for_time_range: {e}", exc_info=True)
            return []

    async def get_pending_files(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get files with 'uploaded' status and mark them as 'processing'.
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List[Dict]: List of file dictionaries with metadata
        """
        from sqlalchemy import update
        
        try:
            async with self.session_scope as session:
                # Start a transaction
                async with session.begin():
                    # Find files with 'uploaded' status
                    result = await session.execute(
                        select(FileModel)
                        .where(FileModel.processing_status == 'uploaded')
                        .order_by(FileModel.created_at.asc())
                        .limit(limit)
                        .with_for_update(skip_locked=True)  # Lock the rows we're updating
                    )
                    files = result.scalars().all()
                    
                    if not files:
                        return []
                    
                    # Mark files as 'processing'
                    file_ids = [file.id for file in files]
                    await session.execute(
                        update(FileModel)
                        .where(FileModel.id.in_(file_ids))
                        .values(processing_status='processing')
                    )
                
                # Convert SQLAlchemy models to dictionaries
                return [
                    {
                        'id': file.id,
                        'user_id': file.user_id,
                        'file_name': file.file_name,
                        'file_type': file.file_type,
                        'file_url': file.file_url,
                        'processing_status': 'processing',  # We just updated this
                        'created_at': file.created_at.isoformat() if file.created_at else None,
                        'metadata': file.metadata or {}
                    }
                    for file in files
                ]
                
        except Exception as e:
            logger.error(f"Error in get_pending_files: {e}", exc_info=True)
            return []

    async def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """
        Get all segment contents for a specific page of a file.
        
        Args:
            file_id: ID of the file
            page_number: Page number to get segments for
            
        Returns:
            List of segment dictionaries with their chunks
        """
        try:
            async with self.session_scope as session:
                result = await session.execute(
                    select(Segment)
                    .options(selectinload(Segment.chunks))
                    .where(
                        Segment.file_id == file_id,
                        Segment.page_number == page_number
                    )
                    .order_by(Segment.id.asc())
                )
                segments = result.scalars().all()
                
                return [
                    {
                        'id': segment.id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'start_time': segment.start_time,
                        'end_time': segment.end_time,
                        'chunks': [
                            {
                                'id': chunk.id,
                                'content': chunk.content,
                                'token_count': chunk.token_count,
                                'metadata': chunk.metadata or {}
                            }
                            for chunk in segment.chunks
                        ],
                        'metadata': segment.metadata or {}
                    }
                    for segment in segments
                ]
                
        except Exception as e:
            logger.error(f"Error in get_segments_for_page: {e}", exc_info=True)
            return []

    async def update_file_status(self, file_id: int, status: str, error_message: str = None) -> bool:
        """
        Update the status of a file.
        
        Args:
            file_id: ID of the file to update
            status: New status (e.g., 'uploaded', 'processing', 'completed', 'failed')
            error_message: Optional error message if the status is 'failed'
            
        Returns:
            bool: True if the update was successful, False otherwise
        """
        from sqlalchemy import update
        
        try:
            async with self.session_scope as session:
                update_data = {
                    'processing_status': status
                }
                
                result = await session.execute(
                    update(FileModel)
                    .where(FileModel.id == file_id)
                    .values(**update_data)
                )
                await session.commit()
                return result.rowcount > 0
                
        except Exception as e:
            logger.error(f"Error updating file status: {e}", exc_info=True)
            try:
                await session.rollback()
            except Exception:
                pass
            return False
            
    async def get_files_by_status(self, status: str) -> List[Dict[str, Any]]:
        """
        Get files by their processing status.
        
        Args:
            status: The processing status to filter by (e.g., 'uploaded', 'processing', 'completed', 'failed')
            
        Returns:
            List of file dictionaries with their data
        """
        try:
            async with self.session_scope as session:
                result = await session.execute(
                    select(FileModel)
                    .where(FileModel.processing_status == status)
                    .order_by(FileModel.created_at.desc())
                )
                files = result.scalars().all()
                
                # Convert SQLAlchemy models to dictionaries
                return [
                    {
                        'id': file.id,
                        'user_id': file.user_id,
                        'file_name': file.file_name,
                        'file_type': file.file_type,
                        'processing_status': file.processing_status,
                        'created_at': file.created_at.isoformat() if file.created_at else None,
                        'metadata': file.metadata or {}
                    }
                    for file in files
                ]
                
        except Exception as e:
            logger.error(f"Error in get_files_by_status: {e}", exc_info=True)
            return []
