"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over the specialized repositories to provide backward compatibility
with the original DocSynthStore interface while maintaining separation of concerns.
"""
from typing import Optional, List, Dict, Any, Tuple
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .user_repository import UserRepository
from .chat_repository import ChatRepository
from .file_repository import FileRepository
from .learning_material_repository import LearningMaterialRepository
from .domain_models import Subscription, CardDetails

logger = logging.getLogger(__name__)


class RepositoryManager:
    """
    Repository manager that coordinates access to all repositories.
    
    Provides a unified interface similar to the original DocSynthStore
    but delegates to specialized repositories following the Single Responsibility Principle.
    """
    
    def __init__(self, database_url: str = None):
        """
        Initialize the repository manager with all required repositories.
        
        Args:
            database_url: The database connection URL. If None, use environment variable.
        """
        self.database_url = database_url
        self.user_repo = UserRepository(database_url)
        self.chat_repo = ChatRepository(database_url)
        self.file_repo = FileRepository(database_url)
        self.learning_material_repo = LearningMaterialRepository(database_url)
    
    # User operations
    def add_user(self, email: str, username: str) -> Optional[int]:
        """Add a new user to the database."""
        return self.user_repo.add_user(email, username)
    
    def get_user_id_from_email(self, email: str) -> Optional[int]:
        """Get user ID from email."""
        return self.user_repo.get_user_id_from_email(email)
    
    def delete_user_account(self, user_id: int) -> bool:
        """Delete a user account and all associated data."""
        return self.user_repo.delete_user_account(user_id)
    
    # Subscription operations
    def add_or_update_subscription(
        self, 
        user_id: int, 
        stripe_customer_id: str, 
        stripe_subscription_id: Optional[str], 
        status: str,
        current_period_end=None, 
        trial_end=None, 
        card_last4=None, 
        card_type=None, 
        exp_month=None, 
        exp_year=None
    ) -> bool:
        """Add or update a user subscription."""
        return self.user_repo.add_or_update_subscription(
            user_id, 
            stripe_customer_id, 
            stripe_subscription_id, 
            status,
            current_period_end, 
            trial_end, 
            card_last4, 
            card_type, 
            exp_month, 
            exp_year
        )
    
    def update_subscription(
        self, 
        stripe_customer_id: str, 
        status: str, 
        current_period_end=None,
        card_last4=None, 
        card_type=None, 
        exp_month=None, 
        exp_year=None
    ) -> bool:
        """Update a subscription by Stripe customer ID."""
        return self.user_repo.update_subscription(
            stripe_customer_id, 
            status, 
            current_period_end,
            card_last4, 
            card_type, 
            exp_month, 
            exp_year
        )
    
    def update_subscription_status(self, stripe_customer_id: str, new_status: str) -> bool:
        """Update subscription status by Stripe customer ID (for webhooks)."""
        return self.user_repo.update_subscription_status(stripe_customer_id, new_status)
    
    def get_subscription(self, user_id: int) -> Optional[Tuple[Subscription, Optional[CardDetails]]]:
        """Get subscription details for a user."""
        return self.user_repo.get_subscription(user_id)
    
    def is_premium_user(self, user_id: int) -> bool:
        """Check if a user has an active premium subscription."""
        return self.user_repo.is_premium_user(user_id)
    
    # Chat operations
    def add_chat_history(self, title: str, user_id: int) -> Optional[int]:
        """Add a new chat history for a user."""
        return self.chat_repo.add_chat_history(title, user_id)
    
    async def add_chat_history_async(self, title: str, user_id: int) -> Optional[int]:
        """Async wrapper for add_chat_history."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_chat_history(title, user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_chat_history: {e}", exc_info=True)
            return None
    
    def get_latest_chat_history_id(self, user_id: int) -> Optional[int]:
        """Get the ID of the most recent chat history for a user."""
        return self.chat_repo.get_latest_chat_history_id(user_id)
    
    async def get_latest_chat_history_id_async(self, user_id: int) -> Optional[int]:
        """Async wrapper for get_latest_chat_history_id."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_latest_chat_history_id(user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_latest_chat_history_id: {e}", exc_info=True)
            return None
    
    def add_message(
        self, 
        content: str, 
        sender: str, 
        user_id: int, 
        chat_history_id: Optional[int] = None
    ) -> Optional[int]:
        """Add a new message to a chat history."""
        return self.chat_repo.add_message(content, sender, user_id, chat_history_id)
    
    async def add_message_async(
        self, 
        content: str, 
        sender: str, 
        user_id: int, 
        chat_history_id: Optional[int] = None
    ) -> Optional[int]:
        """Async wrapper for add_message."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_message(content, sender, user_id, chat_history_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_message: {e}", exc_info=True)
            return None
    
    def get_all_user_chat_histories(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chat histories for a user with latest messages."""
        return self.chat_repo.get_all_user_chat_histories(user_id)
    
    async def get_all_user_chat_histories_async(self, user_id: int) -> List[Dict[str, Any]]:
        """Async wrapper for get_all_user_chat_histories."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_all_user_chat_histories(user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_all_user_chat_histories: {e}", exc_info=True)
            return []
    
    def get_messages_for_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a specific chat history."""
        return self.chat_repo.get_messages_for_chat_history(chat_history_id, user_id)
    
    async def get_messages_for_chat_history_async(self, chat_history_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Async wrapper for get_messages_for_chat_history."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_messages_for_chat_history(chat_history_id, user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_messages_for_chat_history: {e}", exc_info=True)
            return []
    
    def delete_chat_history(self, user_id: int, history_id: int) -> bool:
        """Delete a chat history and all associated messages."""
        return self.chat_repo.delete_chat_history(user_id, history_id)
    
    async def delete_chat_history_async(self, user_id: int, history_id: int) -> bool:
        """Async wrapper for delete_chat_history."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.delete_chat_history(user_id, history_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for delete_chat_history: {e}", exc_info=True)
            return False
    
    def delete_all_user_histories(self, user_id: int) -> bool:
        """Delete all chat histories for a user."""
        return self.chat_repo.delete_all_user_histories(user_id)
    
    async def delete_all_user_histories_async(self, user_id: int) -> bool:
        """Async wrapper for delete_all_user_histories."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.delete_all_user_histories(user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for delete_all_user_histories: {e}", exc_info=True)
            return False
    
    def format_user_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """Format chat history in a way suitable for LLM context."""
        return self.chat_repo.format_user_chat_history(chat_history_id, user_id)
    
    async def format_user_chat_history_async(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """Async wrapper for format_user_chat_history."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.format_user_chat_history(chat_history_id, user_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for format_user_chat_history: {e}", exc_info=True)
            return []
    
    # File operations
    def get_pending_files(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get files with 'uploaded' status and mark them as 'processing'.
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List[Dict]: List of file dictionaries with metadata
        """
        return self.file_repo.get_pending_files(limit)
    
    def update_file_processing_status(self, file_id: int, status: str) -> bool:
        """
        Update the processing status of a file.
        
        Args:
            file_id: ID of the file to update
            status: New status value ('uploaded', 'processing', 'completed', 'failed')
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        return self.file_repo.update_file_processing_status(file_id, status)
    
    def add_file(self, user_id: int, file_name: str, file_url: str, file_type: str = None, **kwargs) -> Optional[int]:
        """Add a new file to the database."""
        # Log any unexpected arguments for debugging
        if kwargs:
            logger.warning(f"Unexpected arguments in add_file: {', '.join(kwargs.keys())}")
        return self.file_repo.add_file(user_id, file_name, file_url, file_type)
    
    async def add_file_async(self, user_id: int, file_name: str, file_url: str, file_type: str = None, **kwargs) -> Optional[int]:
        """Async wrapper for add_file.
        
        Args:
            user_id: ID of the user who owns this file
            file_name: Name of the file
            file_url: URL where the file is stored
            file_type: Type of the file (e.g., 'pdf', 'youtube')
            **kwargs: Additional arguments (ignored for backward compatibility)
            
        Returns:
            int: The ID of the newly created file, or None if creation failed
        """
        # Log any unexpected arguments for debugging
        if kwargs:
            logger.warning(f"Unexpected arguments in add_file_async: {', '.join(kwargs.keys())}")
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self.add_file, user_id, file_name, file_url, file_type
            )
        except Exception as e:
            logger.error(f"Error in async wrapper for add_file: {e}", exc_info=True)
            return None
    
    async def update_file_with_chunks(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict]
    ) -> bool:
        """Store processed file data with embeddings, segments, and metadata."""
        return await self.file_repo.update_file_with_chunks(user_id, filename, file_type, extracted_data)
    
    async def update_file_with_chunks_async(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict]
    ) -> bool:
        """Async wrapper for update_file_with_chunks."""
        try:
            # Directly await the coroutine instead of using run_in_executor
            # since we're already in an async context
            return await self.update_file_with_chunks(user_id, filename, file_type, extracted_data)
        except Exception as e:
            logger.error(f"Error in update_file_with_chunks: {e}", exc_info=True)
            return False
    
    def get_files_for_user(
        self, 
        user_id: int, 
        skip: int = 0, 
        limit: int = 10
    ) -> Dict[str, Any]:
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
        return self.file_repo.get_files_for_user(user_id, skip, limit)
    
    async def get_files_for_user_async(
        self, 
        user_id: int, 
        skip: int = 0, 
        limit: int = 10
    ) -> Dict[str, Any]:
        """Async wrapper for get_files_for_user with pagination.
        
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
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_files_for_user(user_id, skip, limit)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_files_for_user: {e}", exc_info=True)
            return {'items': [], 'total': 0, 'page': 1, 'page_size': limit}
    
    def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data."""
        return self.file_repo.delete_file_entry(user_id, file_id)
    
    async def delete_file_entry_async(self, user_id: int, file_id: int) -> bool:
        """Async wrapper for delete_file_entry."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.delete_file_entry(user_id, file_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for delete_file_entry: {e}", exc_info=True)
            return False
    
    def query_chunks_by_embedding(
        self,
        user_id: int, 
        query_embedding: List[float], 
        top_k: int = 5, 
        similarity_type: str = 'l2'
    ) -> List[Dict[str, Any]]:
        """Retrieves segments with the highest similarity to the query embedding."""
        return self.file_repo.query_chunks_by_embedding(
            user_id, 
            query_embedding, 
            top_k, 
            similarity_type
        )
    
    async def query_chunks_by_embedding_async(
        self,
        user_id: int, 
        query_embedding: List[float], 
        top_k: int = 5, 
        similarity_type: str = 'l2'
    ) -> List[Dict[str, Any]]:
        """Async wrapper for query_chunks_by_embedding."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.query_chunks_by_embedding(user_id, query_embedding, top_k, similarity_type)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for query_chunks_by_embedding: {e}", exc_info=True)
            return []
    
    def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file."""
        return self.file_repo.get_segments_for_page(file_id, page_number)
    
    async def get_segments_for_page_async(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Async wrapper for get_segments_for_page."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_segments_for_page(file_id, page_number)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_segments_for_page: {e}", exc_info=True)
            return []
    
    def get_segments_for_time_range(
        self, 
        file_id: int, 
        start_time: float, 
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get segment contents for a specific time range of a video file."""
        return self.file_repo.get_segments_for_time_range(file_id, start_time, end_time)
    
    async def get_segments_for_time_range_async(
        self, 
        file_id: int, 
        start_time: float, 
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Async wrapper for get_segments_for_time_range."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_segments_for_time_range(file_id, start_time, end_time)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_segments_for_time_range: {e}", exc_info=True)
            return []
    
    def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file record by ID."""
        return self.file_repo.get_file_by_id(file_id)
    
    async def get_file_by_id_async(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Async wrapper for get_file_by_id."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_file_by_id(file_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_file_by_id: {e}", exc_info=True)
            return None
    
    def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename."""
        return self.file_repo.get_file_by_name(user_id, filename)
    
    async def get_file_by_name_async(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Async wrapper for get_file_by_name."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_file_by_name(user_id, filename)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_file_by_name: {e}", exc_info=True)
            return None
    
    def update_file_status(
        self, 
        file_id: int, 
        status: str = None, 
        error_message: str = None
    ) -> bool:
        """
        Update the status of a file in the database.
        
        Args:
            file_id: The ID of the file to update
            status: The new status ('uploaded', 'processing', 'completed', 'failed')
            error_message: Optional error message for failed status
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            # Update the file status using the file repository
            if status:
                success = self.file_repo.update_file_processing_status(file_id, status)
                if not success:
                    logger.error(f"Failed to update status for file {file_id}")
                    return False
            
            # If there's an error message, we need to update it separately
            # since update_file_processing_status doesn't handle error messages
            if error_message:
                success = self._update_file_error_message(file_id, error_message)
                if not success:
                    logger.error(f"Failed to update error message for file {file_id}")
                    return False
            
            logger.info(f"Updated file {file_id} status to {status}" + 
                       (f" with error: {error_message[:100]}..." if error_message else ""))
            
            return True
                
        except Exception as e:
            logger.error(f"Error updating file {file_id} status: {str(e)}", exc_info=True)
            return False
            
    def _update_file_error_message(self, file_id: int, error_message: str) -> bool:
        """Helper method to update the error message for a file."""
        from api.models.orm_models import File
        
        try:
            # Use the unit of work pattern consistent with FileRepository
            with self.file_repo.get_unit_of_work() as uow:
                file = uow.session.query(File).filter(File.id == file_id).first()
                if not file:
                    logger.error(f"File with ID {file_id} not found")
                    return False
                    
                file.error_message = error_message[:500]  # Truncate to avoid DB issues
                uow.session.add(file)
                # Commit is handled by the context manager
                return True
                
        except Exception as e:
            logger.error(f"Error updating error message for file {file_id}: {str(e)}", exc_info=True)
            return False
        
    async def update_file_status_async(
        self, 
        file_id: int, 
        status: str = None, 
        error_message: str = None
    ) -> bool:
        """Async wrapper for update_file_status."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                # Update status if provided
                if status:
                    status_success = await loop.run_in_executor(
                        pool,
                        lambda: self.file_repo.update_file_processing_status(file_id, status)
                    )
                    if not status_success:
                        logger.error(f"Failed to update status for file {file_id}")
                        return False
                
                # Update error message if provided
                if error_message:
                    error_success = await loop.run_in_executor(
                        pool,
                        lambda: self._update_file_error_message(file_id, error_message)
                    )
                    if not error_success:
                        logger.error(f"Failed to update error message for file {file_id}")
                        return False
                
                logger.info(f"Updated file {file_id} status to {status}" + 
                          (f" with error: {error_message[:100]}..." if error_message else ""))
                
                return True
                
        except Exception as e:
            logger.error(f"Error in async update_file_status for file {file_id}: {e}", exc_info=True)
            return False
            
    def update_file_processing_status(
        self, 
        file_id: int, 
        processed: bool, 
        status: str = None, 
        error_message: str = None
    ) -> bool:
        """Update the processing status of a file (deprecated - columns don't exist)."""
        # Convert processed bool to status string for logging purposes
        status_value = status or ("processed" if processed else "pending")
        
        # Log that we're skipping the status update because the columns don't exist
        log_msg = f"Processing status update for file ID {file_id} skipped (columns don't exist in schema)"
        log_msg += f", processed flag: {processed}, status would have been: {status_value}"
        if error_message:
            log_msg += f", error would have been: {error_message[:50]}{'...' if len(error_message) > 50 else ''}"
        
        logger.info(log_msg)
        return True
    
# Learning material operations
def add_key_concept(
    self,
    file_id: int,
    concept_title: str,
    concept_explanation: str,
    source_page_number: Optional[int] = None,
    source_video_timestamp_start_seconds: Optional[int] = None,
    source_video_timestamp_end_seconds: Optional[int] = None,
    is_custom: bool = False
) -> Optional[int]:
    """Add a new key concept associated with a file.
    
    Args:
        file_id: ID of the file this concept belongs to
        concept_title: Title/name of the concept
        concept_explanation: Detailed explanation of the concept
        source_page_number: Page number where the concept appears (for PDFs)
        source_video_timestamp_start_seconds: Start timestamp for video content
        source_video_timestamp_end_seconds: End timestamp for video content
        is_custom: Whether this is a custom (user-created) concept
        
    Returns:
        int: ID of the created concept, or None if creation failed
    """
    from ..schemas.learning_content import KeyConceptCreate
    
    # Create a KeyConceptCreate object with the provided data
    key_concept_data = KeyConceptCreate(
        concept_title=concept_title,
        concept_explanation=concept_explanation,
        source_page_number=source_page_number,
        source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
        source_video_timestamp_end_seconds=source_video_timestamp_end_seconds,
        is_custom=is_custom
    )
    
    # Call the repository method with the KeyConceptCreate object
    result = self.learning_material_repo.add_key_concept(
        file_id=file_id,
        key_concept_data=key_concept_data
    )
    
    # Return the ID of the created concept
    return result.get('id') if result else None

async def add_key_concept_async(
    self,
    file_id: int,
    concept_title: str,
    concept_explanation: str,
    source_page_number: Optional[int] = None,
    source_video_timestamp_start_seconds: Optional[int] = None,
    source_video_timestamp_end_seconds: Optional[int] = None,
    is_custom: bool = False
) -> Optional[int]:
    """Async wrapper for add_key_concept.
    
    Args:
        file_id: ID of the file this concept belongs to
        concept_title: Title/name of the concept
        concept_explanation: Detailed explanation of the concept
        source_page_number: Page number where the concept appears (for PDFs)
        source_video_timestamp_start_seconds: Start timestamp for video content
        source_video_timestamp_end_seconds: End timestamp for video content
        is_custom: Whether this is a custom (user-created) concept
        
    Returns:
        int: ID of the created concept, or None if creation failed
    """
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool,
                lambda: self.add_key_concept(
                    file_id=file_id,
                    concept_title=concept_title,
                    concept_explanation=concept_explanation,
                    source_page_number=source_page_number,
                    source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
                    source_video_timestamp_end_seconds=source_video_timestamp_end_seconds,
                    is_custom=is_custom
                )
            )
            return result
    except Exception as e:
        logger.error(f"Error in async wrapper for add_key_concept: {e}", exc_info=True)
        return None
    
def add_flashcard(
    self, 
    file_id: int, 
    question: str, 
    answer: str, 
    key_concept_id: Optional[int] = None, 
    is_custom: bool = False
) -> Optional[int]:
    """Add a new flashcard linked to a file and optionally a key concept."""
    return self.learning_material_repo.add_flashcard(
        file_id, 
        question, 
        answer, 
        key_concept_id, 
        is_custom
    )
        
async def add_flashcard_async(
    self, 
    file_id: int, 
    question: str, 
    answer: str, 
    key_concept_id: Optional[int] = None, 
    is_custom: bool = False
) -> Optional[int]:
    """Async wrapper for add_flashcard.
    
    Order of parameters must match LearningMaterialRepository.add_flashcard:
    (file_id, question, answer, key_concept_id, is_custom)
    """
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool,
                lambda: self.add_flashcard(
                    file_id, 
                    question, 
                    answer, 
                    key_concept_id, 
                    is_custom
                )
            )
            return result
    except Exception as e:
        logger.error(f"Error in async wrapper for add_flashcard: {e}", exc_info=True)
        return None
    
def add_quiz_question(
    self, 
    file_id: int, 
    key_concept_id: Optional[int] = None, 
    question: str = None, 
    question_type: str = 'MCQ', 
    correct_answer: str = None, 
    distractors: Optional[List[str]] = None,
    quiz_question_data: Optional[Dict] = None
) -> Optional[int]:
    """Add a new quiz question linked to a file and optionally a key concept."""
    if quiz_question_data is not None:
        # New style call with quiz_question_data
        if not isinstance(quiz_question_data, dict):
            quiz_question_data = quiz_question_data.dict()
        return self.learning_material_repo.add_quiz_question(
            file_id=file_id,
            quiz_question_data=quiz_question_data
        )
    else:
        # Old style call with individual parameters
        return self.learning_material_repo.add_quiz_question(
            file_id,
            key_concept_id=key_concept_id,
            question=question,
            question_type=question_type,
            correct_answer=correct_answer,
            distractors=distractors or []
        )
        
    async def add_quiz_question_async(
        self, 
        file_id: int, 
        question: str = None, 
        question_type: str = 'MCQ', 
        correct_answer: str = None, 
        distractors: Optional[List[str]] = None,
        key_concept_id: Optional[int] = None,
        is_custom: bool = False,
        quiz_question_data: Optional[Dict] = None
    ) -> Optional[int]:
        """Async wrapper for add_quiz_question.
        
        Args:
            file_id: ID of the file this question is associated with
            question: The question text
            question_type: Type of question (e.g., 'MCQ', 'True/False')
            correct_answer: The correct answer to the question
            distractors: List of incorrect answer options (for multiple choice)
            key_concept_id: Optional ID of a key concept this question tests
            is_custom: Whether this is a custom (user-created) question
            quiz_question_data: Additional question data in a dictionary
            
        Returns:
            int: ID of the created quiz question, or None if creation failed
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_quiz_question(
                        file_id=file_id,
                        question=question,
                        question_type=question_type,
                        correct_answer=correct_answer,
                        distractors=distractors,
                        key_concept_id=key_concept_id,
                        is_custom=is_custom,
                        quiz_question_data=quiz_question_data
                    )
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_quiz_question: {e}", exc_info=True)
            return None
    
    def add_flashcard(
        self, 
        file_id: int, 
        question: str, 
        answer: str, 
        key_concept_id: Optional[int] = None, 
        is_custom: bool = False
    ) -> Optional[int]:
        """Add a new flashcard linked to a file and optionally a key concept."""
        return self.learning_material_repo.add_flashcard(
            file_id, 
            question, 
            answer, 
            key_concept_id, 
            is_custom
        )
        
    async def add_flashcard_async(
        self, 
        file_id: int, 
        question: str, 
        answer: str, 
        key_concept_id: Optional[int] = None, 
        is_custom: bool = False
    ) -> Optional[int]:
        """Async wrapper for add_flashcard.
        
        Order of parameters must match LearningMaterialRepository.add_flashcard:
        (file_id, question, answer, key_concept_id, is_custom)
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_flashcard(
                        file_id, 
                        question, 
                        answer, 
                        key_concept_id, 
                        is_custom
                    )
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_flashcard: {e}", exc_info=True)
            return None
    
    def add_quiz_question(
        self, 
        file_id: int, 
        key_concept_id: Optional[int] = None, 
        question: str = None, 
        question_type: str = 'MCQ', 
        correct_answer: str = None, 
        distractors: Optional[List[str]] = None,
        quiz_question_data: Optional[Dict] = None
    ) -> Optional[int]:
        """Add a new quiz question linked to a file and optionally a key concept."""
        if quiz_question_data is not None:
            # New style call with quiz_question_data
            if not isinstance(quiz_question_data, dict):
                quiz_question_data = quiz_question_data.dict()
            return self.learning_material_repo.add_quiz_question(
                file_id=file_id,
                quiz_question_data=quiz_question_data
            )
        else:
            # Old style call with individual parameters
            return self.learning_material_repo.add_quiz_question(
                file_id,
                key_concept_id=key_concept_id,
                question=question,
                question_type=question_type,
                correct_answer=correct_answer,
                distractors=distractors or []
            )
        
    async def add_quiz_question_async(
        self, 
        file_id: int, 
        question: str = None, 
        question_type: str = 'MCQ', 
        correct_answer: str = None, 
        distractors: Optional[List[str]] = None,
        key_concept_id: Optional[int] = None,
        is_custom: bool = False,
        quiz_question_data: Optional[Dict] = None
    ) -> Optional[int]:
        """Async wrapper for add_quiz_question.
        
        Supports both the new style (with quiz_question_data) and old style (individual parameters).
        """
        try:
            # Validate parameter types to catch common errors
            if not isinstance(file_id, int) and file_id is not None:
                try:
                    file_id = int(file_id)  # Try to convert to int if possible
                except (ValueError, TypeError):
                    logger.error(f"Invalid file_id: {file_id}, must be an integer")
                    return None
            
            # If using quiz_question_data, validate it
            if quiz_question_data is not None:
                if not isinstance(quiz_question_data, dict):
                    quiz_question_data = quiz_question_data.dict()
                
                # Validate question_type if provided
                if 'question_type' in quiz_question_data and quiz_question_data['question_type'] not in ["MCQ", "TrueFalse"]:
                    logger.warning(f"Unusual question_type: {quiz_question_data['question_type']}, expected 'MCQ' or 'TrueFalse'")
                
                # Validate key_concept_id if provided
                if 'key_concept_id' in quiz_question_data and quiz_question_data['key_concept_id'] is not None:
                    try:
                        if not isinstance(quiz_question_data['key_concept_id'], int):
                            if isinstance(quiz_question_data['key_concept_id'], str) and quiz_question_data['key_concept_id'].isdigit():
                                quiz_question_data['key_concept_id'] = int(quiz_question_data['key_concept_id'])
                            else:
                                logger.error(f"Invalid key_concept_id: {quiz_question_data['key_concept_id']}, must be an integer or None")
                                return None
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error processing key_concept_id: {e}")
                        return None
            else:
                # Old style parameters - validate them
                if question_type not in ["MCQ", "TrueFalse"]:
                    logger.warning(f"Unusual question_type: {question_type}, expected 'MCQ' or 'TrueFalse'")
                
                if key_concept_id is not None and not isinstance(key_concept_id, int):
                    if isinstance(key_concept_id, str) and key_concept_id.isdigit():
                        key_concept_id = int(key_concept_id)
                    else:
                        logger.error(f"Invalid key_concept_id: {key_concept_id}, must be an integer or None")
                        return None
                
                # Create quiz_question_data dict for consistent processing
                quiz_question_data = {
                    'question': question,
                    'question_type': question_type,
                    'correct_answer': correct_answer,
                    'distractors': distractors or [],
                    'key_concept_id': key_concept_id,
                    'is_custom': is_custom
                }
            
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_quiz_question(
                        file_id=file_id,
                        quiz_question_data=quiz_question_data
                    )
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_quiz_question: {e}", exc_info=True)
            return None
    
    def get_key_concepts_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get key concepts for a file."""
        return self.learning_material_repo.get_key_concepts_for_file(file_id)

    def get_key_concept_by_id(self, key_concept_id: int):
        """Get a single key concept by its ID."""
        return self.learning_material_repo.get_key_concept_by_id(key_concept_id)

    def update_key_concept(self, key_concept_id: int, title: Optional[str], explanation: Optional[str]):
        """Update a key concept's title and/or explanation."""
        from ..schemas.learning_content import KeyConceptUpdate
        
        # Create an update dictionary with only the provided fields
        update_data = KeyConceptUpdate()
        if title is not None:
            update_data.title = title
        if explanation is not None:
            update_data.explanation = explanation
            
        return self.learning_material_repo.update_key_concept(key_concept_id, update_data)

    def delete_key_concept(self, key_concept_id: int, user_id: int):
        """Delete a key concept by its ID.
        
        Args:
            key_concept_id: ID of the key concept to delete
            user_id: ID of the user making the request (for authorization)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        return self.learning_material_repo.delete_key_concept(key_concept_id, user_id)
    
    async def get_key_concepts_for_file_async(self, file_id: int) -> List[Dict[str, Any]]:
        """Async wrapper for get_key_concepts_for_file.
        
        Args:
            file_id: ID of the file to get key concepts for
            
        Returns:
            List of key concept dictionaries
        """
        try:
            # Validate parameter types
            if isinstance(file_id, str):
                try:
                    file_id = int(file_id)
                except ValueError:
                    logger.error(f"Invalid file_id: {file_id}, must be convertible to int")
                    return []
            
            # Use run_in_executor to make the synchronous DB operation non-blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.get_key_concepts_for_file(file_id)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for get_key_concepts_for_file: {e}", exc_info=True)
            return []
    
    def get_flashcards_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get flashcards for a file."""
        return self.learning_material_repo.get_flashcards_for_file(file_id)

    def get_flashcard_by_id(self, flashcard_id: int):
        """Get a single flashcard by its ID."""
        return self.learning_material_repo.get_flashcard_by_id(flashcard_id)

    def update_flashcard(self, flashcard_id: int, user_id: int, update_data: Dict[str, Any]):
        """Update a flashcard.
        
        Args:
            flashcard_id: ID of the flashcard to update
            user_id: ID of the user making the request (for authorization)
            update_data: Dictionary containing the fields to update
            
        Returns:
            Updated flashcard ORM object if successful, None otherwise
        """
        from ..schemas.learning_content import FlashcardUpdate
        
        # Create a FlashcardUpdate object from the dictionary
        update_obj = FlashcardUpdate(**update_data)
        
        # Call the repository method with the proper parameters
        return self.learning_material_repo.update_flashcard(
            flashcard_id=flashcard_id,
            user_id=user_id,
            update_data=update_obj
        )

    def delete_flashcard(self, flashcard_id: int, user_id: int):
        """Delete a flashcard by its ID.
        
        Args:
            flashcard_id: ID of the flashcard to delete
            user_id: ID of the user making the request (for authorization)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        return self.learning_material_repo.delete_flashcard(flashcard_id, user_id)
    
    def delete_quiz_question(self, quiz_question_id: int, user_id: int) -> bool:
        """Delete a quiz question by its ID.
        
        Args:
            quiz_question_id: ID of the quiz question to delete
            user_id: ID of the user making the request (for authorization)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        return self.learning_material_repo.delete_quiz_question(quiz_question_id, user_id)

    def get_quiz_questions_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get all quiz questions for a file."""
        return self.learning_material_repo.get_quiz_questions_for_file(file_id)

    def get_quiz_question_by_id(self, quiz_question_id: int):
        """Get a single quiz question by its ID."""
        return self.learning_material_repo.get_quiz_question_by_id(quiz_question_id)

    def update_quiz_question(self, quiz_question_id: int, user_id: int, update_data: Dict[str, Any]):
        """Update a quiz question.
        
        Args:
            quiz_question_id: ID of the quiz question to update
            user_id: ID of the user making the request (for authorization)
            update_data: Dictionary containing the fields to update
            
        Returns:
            Updated quiz question ORM object if successful, None otherwise
        """
        from ..schemas.learning_content import QuizQuestionUpdate
        
        # Create a QuizQuestionUpdate object from the dictionary
        update_obj = QuizQuestionUpdate(**update_data)
        
        # Call the repository method with the proper parameters
        return self.learning_material_repo.update_quiz_question(
            quiz_question_id=quiz_question_id,
            user_id=user_id,
            update_data=update_obj
        )

    def delete_quiz_question(self, quiz_question_id: int):
        """Delete a quiz question by its ID."""
        return self.learning_material_repo.delete_quiz_question(quiz_question_id)
        
    def add_key_concept(self, file_id: int, concept_title: str, concept_explanation: str, **kwargs) -> Optional[int]:
        """Add a new key concept for a file."""
        return self.learning_material_repo.add_key_concept(
            file_id=file_id,
            concept_title=concept_title,
            concept_explanation=concept_explanation,
            source_page_number=kwargs.get('source_page_number'),
            source_video_timestamp_start_seconds=kwargs.get('source_video_timestamp_start_seconds'),
            source_video_timestamp_end_seconds=kwargs.get('source_video_timestamp_end_seconds'),

        )
    
    async def add_key_concept_async(self, file_id: int, concept_title: str, concept_explanation: str, **kwargs) -> Optional[int]:
        """Async wrapper for add_key_concept.
        
        Args:
            file_id: ID of the file this concept belongs to
            concept_title: Title/name of the concept
            concept_explanation: Detailed explanation of the concept
            **kwargs: Additional parameters like source_page_number, timestamps, etc.
            
        Returns:
            int: The ID of the newly created concept, or None if creation failed
        """
        try:
            # Validate parameter types
            if isinstance(file_id, str):
                try:
                    file_id = int(file_id)
                except ValueError:
                    logger.error(f"Invalid file_id: {file_id}, must be convertible to int")
                    return None
            
            # Use run_in_executor to make the synchronous DB operation non-blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_key_concept(
                        file_id=file_id,
                        concept_title=concept_title,
                        concept_explanation=concept_explanation,
                        **kwargs
                    )
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_key_concept: {e}", exc_info=True)
            return None


# Global instance of RepositoryManager
_repository_manager_instance = None

def get_repository_manager(database_url: str = None) -> "RepositoryManager":
    """
    Get or create a singleton instance of RepositoryManager.
    
    Args:
        database_url: Optional database URL. If not provided, constructs from environment variables.
        
    Returns:
        RepositoryManager: The singleton instance
    """
    global _repository_manager_instance
    if _repository_manager_instance is None:
        if database_url is None:
            import os
            # Construct database URL from individual environment variables
            database_config = {
                'dbname': os.getenv("DATABASE_NAME"),
                'user': os.getenv("DATABASE_USER"),
                'password': os.getenv("DATABASE_PASSWORD"),
                'host': os.getenv("DATABASE_HOST"),
                'port': os.getenv("DATABASE_PORT"),
            }
            
            if not all(database_config.values()):
                raise ValueError("Missing required database configuration. Please set all DATABASE_* environment variables.")
                
            database_url = (
                f"postgresql://{database_config['user']}:{database_config['password']}"
                f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
            )
            
        _repository_manager_instance = RepositoryManager(database_url)
    return _repository_manager_instance

