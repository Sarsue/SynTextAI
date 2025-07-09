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
    def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file to the database."""
        return self.file_repo.add_file(user_id, file_name, file_url)
    
    async def add_file_async(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Async wrapper for add_file."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_file(user_id, file_name, file_url)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for add_file: {e}", exc_info=True)
            return None
    
    def update_file_with_chunks(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict]
    ) -> bool:
        """Store processed file data with embeddings, segments, and metadata."""
        return self.file_repo.update_file_with_chunks(user_id, filename, file_type, extracted_data)
    
    async def update_file_with_chunks_async(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict]
    ) -> bool:
        """Async wrapper for update_file_with_chunks."""
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.update_file_with_chunks(user_id, filename, file_type, extracted_data)
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for update_file_with_chunks: {e}", exc_info=True)
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
        """Update the status of a file (deprecated - columns don't exist)."""
        # Log that we're skipping the status update because the columns don't exist
        log_msg = f"Status update for file ID {file_id} skipped (columns don't exist in schema)"
        if status:
            log_msg += f", status would have been: {status}"
        if error_message:
            log_msg += f", error would have been: {error_message[:50]}{'...' if len(error_message) > 50 else ''}"
        logger.info(log_msg)
        return True
        
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
                result = await loop.run_in_executor(
                    pool,
                    self.update_file_status,
                    file_id, status, error_message
                )
                return result
        except Exception as e:
            logger.error(f"Error in async wrapper for update_file_status: {e}", exc_info=True)
            return False
    
    # For backward compatibility with the old method name
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
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Add a new key concept associated with a file.
        
        Parameter order must match LearningMaterialRepository.add_key_concept:
        (file_id, concept_title, concept_explanation, source_page_number, source_video_timestamp_start_seconds, source_video_timestamp_end_seconds)
        """
        return self.learning_material_repo.add_key_concept(
            file_id,
            concept_title,
            concept_explanation,
            source_page_number,
            source_video_timestamp_start_seconds,
            source_video_timestamp_end_seconds
        )
        
    async def add_key_concept_async(
        self,
        file_id: int,
        concept_title: str,
        concept_explanation: str,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Async wrapper for add_key_concept.
        
        Parameter order must match LearningMaterialRepository.add_key_concept:
        (file_id, concept_title, concept_explanation, source_page_number, source_video_timestamp_start_seconds, source_video_timestamp_end_seconds)
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_key_concept(
                        file_id,
                        concept_title,
                        concept_explanation,
                        source_page_number,
                        source_video_timestamp_start_seconds,
                        source_video_timestamp_end_seconds
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
        key_concept_id: Optional[int], 
        question: str, 
        question_type: str, 
        correct_answer: str, 
        distractors: Optional[List[str]] = None
    ) -> Optional[int]:
        """Add a new quiz question linked to a file and optionally a key concept."""
        return self.learning_material_repo.add_quiz_question(
            file_id, 
            question, 
            question_type, 
            correct_answer, 
            distractors,
            key_concept_id
        )
        
    async def add_quiz_question_async(
        self, 
        file_id: int, 
        question: str, 
        question_type: str, 
        correct_answer: str, 
        distractors: Optional[List[str]] = None,
        key_concept_id: Optional[int] = None,
        is_custom: bool = False
    ) -> Optional[int]:
        """Async wrapper for add_quiz_question.
        
        IMPORTANT: The parameter order here doesn't match the underlying method.
        We need to re-map the parameters in the correct order when calling add_quiz_question.
        """
        try:
            # Validate parameter types to catch common errors
            if not isinstance(file_id, int) and file_id is not None:
                try:
                    file_id = int(file_id)  # Try to convert to int if possible
                except (ValueError, TypeError):
                    logger.error(f"Invalid file_id: {file_id}, must be an integer")
                    return None
            
            # Most critical validation: key_concept_id must be an integer or None
            if key_concept_id is not None:
                if not isinstance(key_concept_id, int):
                    if isinstance(key_concept_id, str) and key_concept_id.isdigit():
                        # Convert string numeric ID to int
                        key_concept_id = int(key_concept_id)
                    else:
                        # This is the critical error we're seeing - reject invalid types
                        logger.error(f"Invalid key_concept_id: {key_concept_id}, must be an integer or None")
                        return None
            
            # Ensure that question_type is a valid string            
            if question_type not in ["MCQ", "TrueFalse"]:
                logger.warning(f"Unusual question_type: {question_type}, expected 'MCQ' or 'TrueFalse'")
            
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    lambda: self.add_quiz_question(
                        file_id,
                        key_concept_id,  # This is the 2nd param in add_quiz_question 
                        question,        # This is the 3rd param in add_quiz_question
                        question_type,   # This is the 4th param in add_quiz_question
                        correct_answer,  # This is the 5th param in add_quiz_question
                        distractors      # This is the 6th param in add_quiz_question
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
