"""
Repository for file-related database operations.
"""
from typing import Optional, List, Dict, Any, Tuple
import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import numpy as np
from scipy.spatial.distance import cosine, euclidean

from .base_repository import BaseRepository
from .domain_models import File, Segment, Chunk

# Import ORM models from the new models module
from models import File as FileORM
from models import Segment as SegmentORM
from models import Chunk as ChunkORM

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
        session = self.get_session()
        try:
            new_file = FileORM(
                user_id=user_id,
                file_name=file_name,
                file_url=file_url
                # status column removed as it doesn't exist in the database schema
            )
            session.add(new_file)
            session.commit()
            logger.info(f"Added new file {file_name} (ID: {new_file.id}) for user {user_id}")
            return new_file.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding file: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    def update_file_with_chunks(self, user_id: int, filename: str, file_type: str, extracted_data: List[Dict]) -> bool:
        """Store processed file data with embeddings, segments, and metadata.
        
        Args:
            user_id: ID of the user who owns the file
            filename: Name of the file
            file_type: Type of file (pdf, video, etc.)
            extracted_data: Processed data containing chunks and embeddings
            
        Returns:
            bool: True if successful, False otherwise
        """
        session = self.get_session()
        try:
            # Get or create the file record
            file = session.query(FileORM).filter(
                FileORM.user_id == user_id,
                FileORM.file_name == filename
            ).first()
            
            if not file:
                file = FileORM(
                    user_id=user_id,
                    file_name=filename,
                    file_url=""  # URL might be added later
                    # status column removed as it doesn't exist in the database schema
                )
                session.add(file)
                session.flush()  # To get the ID
            
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
                
                session.add(segment)
                session.flush()  # To get the segment ID
                
                # Process chunks within this segment
                if 'chunks' in segment_data:
                    for chunk_data in segment_data['chunks']:
                        chunk = ChunkORM(
                            file_id=file.id,
                            segment_id=segment.id,
                            content=chunk_data['content'],
                            embedding=chunk_data.get('embedding'),
                            file_type=file_type
                        )
                        session.add(chunk)
            
            # Status update removed - status column doesn't exist in database schema
            # file.status = "processed"
            file.file_type = file_type
            
            session.commit()
            logger.info(f"Successfully stored processed data for file: {filename}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error storing processed data: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def get_files_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all files for a user.
        
        Args:
            user_id: ID of the user
            
        Returns:
            List[Dict]: List of file records with metadata
        """
        session = self.get_session()
        try:
            files = session.query(FileORM).filter(
                FileORM.user_id == user_id
            ).order_by(FileORM.created_at.desc()).all()
            
            result = []
            for file in files:
                file_dict = {
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "created_at": file.created_at.isoformat() if file.created_at else None
                    # status and error_message removed - don't exist in database schema
                }
                result.append(file_dict)
            
            return result
        except Exception as e:
            logger.error(f"Error getting files for user {user_id}: {e}", exc_info=True)
            return []
        finally:
            session.close()
    
    def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data.
        
        Args:
            user_id: ID of the user who owns the file
            file_id: ID of the file to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        session = self.get_session()
        try:
            # Check if the file exists and belongs to the specified user
            file_obj = session.query(FileORM).filter(
                FileORM.id == file_id,
                FileORM.user_id == user_id
            ).first()
            
            if not file_obj:
                logger.warning(f"File {file_id} not found or not owned by user {user_id}")
                return False
            
            # Delete the file (cascade should handle related entities)
            session.delete(file_obj)
            session.commit()
            logger.info(f"Successfully deleted file {file_id} for user {user_id} with cascade")
            return True
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
            
            # Fallback to direct SQL deletion if ORM cascade fails
            try:
                # Start with associated entities and work our way up
                # Delete flashcards
                session.execute(text(f"DELETE FROM flashcards WHERE file_id = {file_id}"))
                # Delete quiz questions
                session.execute(text(f"DELETE FROM quiz_questions WHERE file_id = {file_id}"))
                # Delete key concepts
                session.execute(text(f"DELETE FROM key_concepts WHERE file_id = {file_id}"))
                # Delete chunks
                session.execute(text(f"DELETE FROM chunks WHERE file_id = {file_id}"))
                # Delete segments
                session.execute(text(f"DELETE FROM segments WHERE file_id = {file_id}"))
                # Finally delete the file
                session.execute(text(f"DELETE FROM files WHERE id = {file_id} AND user_id = {user_id}"))
                
                session.commit()
                logger.info(f"Successfully deleted file {file_id} for user {user_id} using manual SQL deletion")
                return True
                
            except Exception as sql_error:
                session.rollback()
                logger.error(f"SQL fallback error deleting file {file_id}: {sql_error}", exc_info=True)
                return False
        finally:
            session.close()
    
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
        session = self.get_session()
        try:
            # Get all chunks for the user's files
            files = session.query(FileORM).filter(FileORM.user_id == user_id).all()
            if not files:
                return []
                
            file_ids = [file.id for file in files]
            
            # Get all chunks with embeddings
            chunks = session.query(ChunkORM).filter(
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
        finally:
            session.close()
    
    def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file.
        
        Args:
            file_id: ID of the file
            page_number: Page number to retrieve segments for
            
        Returns:
            List[Dict]: List of segments
        """
        session = self.get_session()
        try:
            segments = session.query(SegmentORM).filter(
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
        finally:
            session.close()
    
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
        session = self.get_session()
        try:
            # Build base query
            query = session.query(SegmentORM).filter(SegmentORM.file_id == file_id)
            
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
        finally:
            session.close()
    
    def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file record by ID.
        
        Args:
            file_id: ID of the file
            
        Returns:
            Dict: File record if found, None otherwise
        """
        session = self.get_session()
        try:
            file = session.query(FileORM).filter(FileORM.id == file_id).first()
            if not file:
                return None
                
            return {
                'id': file.id,
                'user_id': file.user_id,
                'file_name': file.file_name,
                'file_url': file.file_url,
                'created_at': file.created_at.isoformat() if file.created_at else None
                # status and error_message fields removed - don't exist in database schema
            }
        except Exception as e:
            logger.error(f"Error getting file by ID {file_id}: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename.
        
        Args:
            user_id: ID of the user
            filename: Name of the file
            
        Returns:
            Dict: File record if found, None otherwise
        """
        session = self.get_session()
        try:
            file = session.query(FileORM).filter(
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
                # status and error_message fields removed - don't exist in database schema
            }
        except Exception as e:
            logger.error(f"Error getting file by name: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    def update_file_status(self, file_id: int, status: str = None, error_message: str = None) -> bool:
        """Update the status of a file.
        
        Args:
            file_id: ID of the file to update
            status: New status value (e.g., 'success', 'failed', 'warning')
            error_message: Optional error message when processing failed
            
        Returns:
            bool: True if successful, False otherwise
            
        Note:
            This method is maintained for API compatibility but no longer updates status fields
            as they don't exist in the database schema.
        """
        # Log that we're skipping the status update because the columns don't exist
        log_msg = f"Status update for file ID {file_id} skipped (columns don't exist in schema)"
        if status:
            log_msg += f", status would have been: {status}"
        if error_message:
            log_msg += f", error would have been: {error_message[:50]}{'...' if len(error_message) > 50 else ''}"
        logger.info(log_msg)
        
        # Return true to avoid breaking any code that expects this to work
        return True
