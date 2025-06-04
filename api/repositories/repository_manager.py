"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over the specialized repositories to provide backward compatibility
with the original DocSynthStore interface while maintaining separation of concerns.
"""
from typing import Optional, List, Dict, Any, Tuple
import logging

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
    
    def get_latest_chat_history_id(self, user_id: int) -> Optional[int]:
        """Get the ID of the most recent chat history for a user."""
        return self.chat_repo.get_latest_chat_history_id(user_id)
    
    def add_message(
        self, 
        content: str, 
        sender: str, 
        user_id: int, 
        chat_history_id: Optional[int] = None
    ) -> Optional[int]:
        """Add a new message to a chat history."""
        return self.chat_repo.add_message(content, sender, user_id, chat_history_id)
    
    def get_all_user_chat_histories(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chat histories for a user with latest messages."""
        return self.chat_repo.get_all_user_chat_histories(user_id)
    
    def get_messages_for_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a specific chat history."""
        return self.chat_repo.get_messages_for_chat_history(chat_history_id, user_id)
    
    def delete_chat_history(self, user_id: int, history_id: int) -> bool:
        """Delete a chat history and all associated messages."""
        return self.chat_repo.delete_chat_history(user_id, history_id)
    
    def delete_all_user_histories(self, user_id: int) -> bool:
        """Delete all chat histories for a user."""
        return self.chat_repo.delete_all_user_histories(user_id)
    
    def format_user_chat_history(self, chat_history_id: int, user_id: int) -> List[Dict[str, str]]:
        """Format chat history in a way suitable for LLM context."""
        return self.chat_repo.format_user_chat_history(chat_history_id, user_id)
    
    # File operations
    def add_file(self, user_id: int, file_name: str, file_url: str) -> Optional[int]:
        """Add a new file to the database."""
        return self.file_repo.add_file(user_id, file_name, file_url)
    
    def update_file_with_chunks(
        self, 
        user_id: int, 
        filename: str, 
        file_type: str, 
        extracted_data: List[Dict]
    ) -> bool:
        """Store processed file data with embeddings, segments, and metadata."""
        return self.file_repo.update_file_with_chunks(user_id, filename, file_type, extracted_data)
    
    def get_files_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all files for a user."""
        return self.file_repo.get_files_for_user(user_id)
    
    def delete_file_entry(self, user_id: int, file_id: int) -> bool:
        """Delete a file and all associated data."""
        return self.file_repo.delete_file_entry(user_id, file_id)
    
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
    
    def get_segments_for_page(self, file_id: int, page_number: int) -> List[Dict[str, Any]]:
        """Get all segment contents for a specific page of a file."""
        return self.file_repo.get_segments_for_page(file_id, page_number)
    
    def get_segments_for_time_range(
        self, 
        file_id: int, 
        start_time: float, 
        end_time: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Get segment contents for a specific time range of a video file."""
        return self.file_repo.get_segments_for_time_range(file_id, start_time, end_time)
    
    def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get a file record by ID."""
        return self.file_repo.get_file_by_id(file_id)
    
    def get_file_by_name(self, user_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Get a file record by user ID and filename."""
        return self.file_repo.get_file_by_name(user_id, filename)
    
    def update_file_status(
        self, 
        file_id: int, 
        status: str = None, 
        error_message: str = None
    ) -> bool:
        """Update the status of a file."""
        return self.file_repo.update_file_status(file_id, status, error_message)
    
    # For backward compatibility with the old method name
    def update_file_processing_status(
        self, 
        file_id: int, 
        processed: bool, 
        status: str = None, 
        error_message: str = None
    ) -> bool:
        """Update the processing status of a file (backward compatibility)."""
        # Convert processed bool to status string
        status_value = status or ("processed" if processed else "pending")
        return self.file_repo.update_file_status(file_id, status_value, error_message)
    
    # Learning material operations
    def add_key_concept(
        self,
        file_id: int,
        concept_title: str,
        concept_explanation: str,
        source_text: Optional[str] = None,
        source_start: Optional[int] = None,
        source_end: Optional[int] = None,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Add a new key concept associated with a file."""
        return self.learning_material_repo.add_key_concept(
            file_id,
            concept_title,
            concept_explanation,
            source_text,
            source_start,
            source_end,
            source_page_number,
            source_video_timestamp_start_seconds,
            source_video_timestamp_end_seconds
        )
    
    def add_flashcard(
        self, 
        file_id: int, 
        key_concept_id: int, 
        question: str, 
        answer: str, 
        is_custom: bool = False
    ) -> Optional[int]:
        """Add a new flashcard linked to a file and key concept."""
        return self.learning_material_repo.add_flashcard(
            file_id, 
            key_concept_id, 
            question, 
            answer, 
            is_custom
        )
    
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
            key_concept_id, 
            question, 
            question_type, 
            correct_answer, 
            distractors
        )
    
    def get_key_concepts_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get key concepts for a file."""
        return self.learning_material_repo.get_key_concepts_for_file(file_id)
    
    def get_flashcards_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get flashcards for a file."""
        return self.learning_material_repo.get_flashcards_for_file(file_id)
    
    def get_quiz_questions_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get all quiz questions for a file."""
        return self.learning_material_repo.get_quiz_questions_for_file(file_id)
