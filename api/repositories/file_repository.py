"""
Repository for file-related database operations.
"""
from typing import Optional, List, Dict, Any, Tuple
import logging
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError
import numpy as np
import asyncio
from concurrent.futures import ThreadPoolExecutor
from scipy.spatial.distance import cosine, euclidean

from .base_repository import BaseRepository
from .domain_models import File, Segment, Chunk

# Import ORM models from the new models module
from models import File as FileORM, Chunk as ChunkORM, KeyConcept as KeyConceptORM
from models import Segment as SegmentORM

logger = logging.getLogger(__name__)


class FileRepository(BaseRepository):
    """Repository for file-related database operations."""
    
    def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file to the database.
        
        Args:
            user_id: ID of the user who owns this file
            file_name: Name of the file
            file_url: URL where the file is stored
            
        Returns:
            int: The ID of the newly created file, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                new_file = FileORM(
                    user_id=user_id,
                    file_name=file_name,
                    file_url=file_url
                )
                uow.session.add(new_file)
                # Flush the session to get the ID assigned by the database
                uow.session.flush()
                # Now we can access the ID
                logger.info(f"Added new file {file_name} (ID: {new_file.id}) for user {user_id}")
                return new_file.id
            except Exception as e:
                # No need for rollback - handled by UnitOfWork
                logger.error(f"Error adding file: {e}", exc_info=True)
                return None
            # No need for finally/close - handled by UnitOfWork
    
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
        # We'll use run_in_executor to make a synchronous operation asynchronous
        # This prevents blocking the event loop while database operations are in progress
        try:
            # Create a ThreadPoolExecutor to handle the synchronous database operations
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                # Run the synchronous database operation in a separate thread
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
                        file_url=""  # URL might be added later
                    )
                    uow.session.add(file)
                    uow.session.flush()  # To get the ID
                
                # Process segments and chunks
                for segment_data in extracted_data:
                    # Create segment
                    segment = SegmentORM(
                        file_id=file.id,
                        content=segment_data.get('content', ''),
                        page_number=segment_data.get('page_number')
                    )
                    
                    # Handle metadata - could be start/end times for video, etc.
                    meta_data = {}
                    for key in segment_data:
                        if key not in ['content', 'page_number', 'chunks']:
                            meta_data[key] = segment_data[key]
                    
                    if meta_data:
                        segment.meta_data = meta_data
                    
                    uow.session.add(segment)
                    uow.session.flush()  # To get the segment ID
                    
                    # Process chunks within this segment
                    if 'chunks' in segment_data:
                        for chunk_data in segment_data['chunks']:
                            chunk = ChunkORM(
                                segment_id=segment.id,
                                content=chunk_data.get('content', ''),
                                embedding=chunk_data.get('embedding')
                            )
                            uow.session.add(chunk)
                
                # Commit handled by UnitOfWork
                return True
            except IntegrityError as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Integrity error updating file with chunks: {e}", exc_info=True)
                return False
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error updating file with chunks: {e}", exc_info=True)
                return False
    
    def get_files_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all files for a user.
        
        Args:
            user_id: ID of the user
            
        Returns:
            List[Dict]: List of file records with metadata
        """
        with self.get_unit_of_work() as uow:
            try:
                from sqlalchemy import func
                
                # Fetch files with chunk count and key concepts count in a single query (more efficient)
                files = uow.session.query(
                    FileORM.id, 
                    FileORM.file_name, 
                    FileORM.file_url,
                    FileORM.created_at,
                    func.count(ChunkORM.id).label('chunk_count'),
                    func.count(KeyConceptORM.id).label('key_concepts_count')
                ).outerjoin(ChunkORM, ChunkORM.file_id == FileORM.id)\
                .outerjoin(KeyConceptORM, KeyConceptORM.file_id == FileORM.id)\
                .filter(FileORM.user_id == user_id)\
                .group_by(FileORM.id, FileORM.file_name, FileORM.file_url, FileORM.created_at)\
                .order_by(FileORM.created_at.desc())\
                .all()
                
                result = []
                for file in files:
                    # A file is considered fully processed only when it has key concepts
                    is_processed = file[5] > 0  # key_concepts_count > 0
                    
                    file_dict = {
                        "id": file[0],                 # FileORM.id
                        "file_name": file[1],         # FileORM.file_name - keep for backend compatibility
                        "name": file[1],              # Match original field name for frontend
                        "file_url": file[2],          # FileORM.file_url - keep for backend compatibility
                        "publicUrl": file[2],         # Match original field name for frontend
                        "processed": is_processed,     # Using the original definition (has chunks)
                        "created_at": file[3].isoformat() if file[3] else None  # FileORM.created_at
                    }
                    
                    result.append(file_dict)
                
                return result
            except Exception as e:
                logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
                return []
    
    def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data.
        
        Args:
            user_id: ID of the user who owns the file
            file_id: ID of the file to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        # First attempt: use ORM cascade deletion with unit of work
        with self.get_unit_of_work() as uow:
            try:
                # Check if the file exists and belongs to the specified user
                file_obj = uow.session.query(FileORM).filter(
                    FileORM.id == file_id,
                    FileORM.user_id == user_id
                ).first()
                
                if not file_obj:
                    logger.warning(f"File {file_id} not found or not owned by user {user_id}")
                    return False
                
                # Delete the file (cascade should handle related entities)
                uow.session.delete(file_obj)
                # Commit handled by UnitOfWork
                logger.info(f"Successfully deleted file {file_id} for user {user_id} with cascade")
                return True
            
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
        
        # Fallback: If ORM deletion failed, try SQL deletion with a new unit of work
        with self.get_unit_of_work() as uow:
            try:
                # Start with associated entities and work our way up
                # Delete flashcards
                uow.session.execute(text(f"DELETE FROM flashcards WHERE file_id = {file_id}"))
                # Delete quiz questions
                uow.session.execute(text(f"DELETE FROM quiz_questions WHERE file_id = {file_id}"))
                # Delete key concepts
                uow.session.execute(text(f"DELETE FROM key_concepts WHERE file_id = {file_id}"))
                # Delete chunks
                uow.session.execute(text(f"DELETE FROM chunks WHERE file_id = {file_id}"))
                # Delete segments
                uow.session.execute(text(f"DELETE FROM segments WHERE file_id = {file_id}"))
                # Finally delete the file
                uow.session.execute(text(f"DELETE FROM files WHERE id = {file_id} AND user_id = {user_id}"))
                
                # Commit handled by UnitOfWork
                logger.info(f"Successfully deleted file {file_id} for user {user_id} using manual SQL deletion")
                return True
                
            except Exception as sql_error:
                # Rollback handled by UnitOfWork
                logger.error(f"SQL fallback error deleting file {file_id}: {sql_error}", exc_info=True)
                return False
    
    def query_chunks_by_embedding(
        self,
        user_id: int, 
        query_embedding: List[float], 
        top_k: int = 5, 
        similarity_type: str = 'l2'
    ) -> List[Dict]:
        """Retrieves segments with the highest similarity to the query embedding.
        
        Args:
            user_id: ID of the user
            query_embedding: Embedding of the user's query
            top_k: Number of top results to return
            similarity_type: Type of similarity calculation ('l2', 'cosine')
            
        Returns:
            List[Dict]: List of segments with similarity scores
        """
        with self.get_unit_of_work() as uow:
            try:
                # Get all chunks for the user's files
                files = uow.session.query(FileORM).filter(FileORM.user_id == user_id).all()
                if not files:
                    return []
                    
                file_ids = [file.id for file in files]
                
                # Get all chunks with embeddings
                chunks = uow.session.query(ChunkORM).filter(
                    ChunkORM.file_id.in_(file_ids),
                    ChunkORM.embedding != None
                ).all()
                
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
                        'similarity': float(similarity)  # Convert from numpy to Python float
                    })
                    
                # Sort by similarity and get top_k results
                results.sort(key=lambda x: x['similarity'], reverse=True)
                return results[:top_k]
                
            except Exception as e:
                logger.error(f"Error querying chunks by embedding: {e}", exc_info=True)
                return []
    
    def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file.
        
        Args:
            file_id: ID of the file
            page_number: Page number to retrieve segments for
            
        Returns:
            List[Dict]: List of segments
        """
        with self.get_unit_of_work() as uow:
            try:
                segments = uow.session.query(SegmentORM).filter(
                    SegmentORM.file_id == file_id,
                    SegmentORM.page_number == page_number
                ).all()
                
                result = []
                for segment in segments:
                    meta = {}
                    if segment.meta_data:
                        meta = segment.meta_data
                    
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'page_number': segment.page_number,
                        'meta_data': meta
                    })
                
                return result
            except Exception as e:
                logger.error(f"Error getting segments for page: {e}", exc_info=True)
                return []
    
    def get_segments_for_time_range(
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
        with self.get_unit_of_work() as uow:
            try:
                # Build base query
                query = uow.session.query(SegmentORM).filter(SegmentORM.file_id == file_id)
                
                # Apply filter for time range
                if end_time:
                    # Get segments that overlap with the time range
                    # A segment overlaps if:
                    # - Its start time is before the end time of the range AND
                    # - Its end time is after the start time of the range
                    query = query.filter(
                        SegmentORM.meta_data['start_time'].astext.cast(float) <= end_time,
                        SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                    )
                else:
                    # Just find closest segment to the given time point
                    query = query.filter(
                        SegmentORM.meta_data['start_time'].astext.cast(float) <= start_time,
                        SegmentORM.meta_data['end_time'].astext.cast(float) >= start_time
                    )
                
                segments = query.all()
                result = []
                
                for segment in segments:
                    meta = {}
                    if segment.meta_data:
                        meta = segment.meta_data
                    
                    result.append({
                        'id': segment.id,
                        'content': segment.content,
                        'meta_data': meta
                    })
                
                return result
            except Exception as e:
                logger.error(f"Error getting segments for time range: {e}", exc_info=True)
                return []
    
    def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file record by ID.
        
        Args:
            file_id: ID of the file
            
        Returns:
            Dict: File record if found, None otherwise
        """
        with self.get_unit_of_work() as uow:
            try:
                file = uow.session.query(FileORM).filter(FileORM.id == file_id).first()
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
                logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
                return None
    
    def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename.
        
        Args:
            user_id: ID of the user
            filename: Name of the file
            
        Returns:
            Dict: File record if found, None otherwise
        """
        with self.get_unit_of_work() as uow:
            try:
                file = uow.session.query(FileORM).filter(
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
                    'created_at': file.created_at.isoformat() if file.created_at else None
                }
            except Exception as e:
                logger.error(f"Error getting file by name: {e}", exc_info=True)
                return None
