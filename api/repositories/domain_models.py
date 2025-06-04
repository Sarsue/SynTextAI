"""
Domain models for the application.
These models represent the core entities of our domain and are separate from their database implementation.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any


class User:
    """User domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        email: str = "",
        username: str = ""
    ):
        self.id = id
        self.email = email
        self.username = username
        
    def __repr__(self) -> str:
        return f"User(id={self.id}, email='{self.email}', username='{self.username}')"


class Subscription:
    """Subscription domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        user_id: Optional[int] = None,
        stripe_customer_id: str = "",
        stripe_subscription_id: Optional[str] = None,
        status: str = "",
        current_period_end: Optional[datetime] = None,
        trial_end: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None
    ):
        self.id = id
        self.user_id = user_id
        self.stripe_customer_id = stripe_customer_id
        self.stripe_subscription_id = stripe_subscription_id
        self.status = status
        self.current_period_end = current_period_end
        self.trial_end = trial_end
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()
        self.card_details = None
    
    def __repr__(self) -> str:
        return f"Subscription(id={self.id}, user_id={self.user_id}, status='{self.status}')"


class CardDetails:
    """Card details domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        subscription_id: Optional[int] = None,
        card_last4: str = "",
        card_type: str = "",
        exp_month: int = 0,
        exp_year: int = 0,
        created_at: Optional[datetime] = None
    ):
        self.id = id
        self.subscription_id = subscription_id
        self.card_last4 = card_last4
        self.card_type = card_type
        self.exp_month = exp_month
        self.exp_year = exp_year
        self.created_at = created_at or datetime.utcnow()
    
    def __repr__(self) -> str:
        return f"CardDetails(id={self.id}, card_type='{self.card_type}', last4='{self.card_last4}')"


class ChatHistory:
    """Chat history domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        user_id: Optional[int] = None,
        title: str = "Untitled"
    ):
        self.id = id
        self.user_id = user_id
        self.title = title
        self.messages = []
    
    def __repr__(self) -> str:
        return f"ChatHistory(id={self.id}, title='{self.title}')"


class Message:
    """Message domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        user_id: Optional[int] = None,
        chat_history_id: Optional[int] = None,
        content: str = "",
        sender: str = "",
        timestamp: Optional[datetime] = None
    ):
        self.id = id
        self.user_id = user_id
        self.chat_history_id = chat_history_id
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or datetime.utcnow()
    
    def __repr__(self) -> str:
        return f"Message(id={self.id}, sender='{self.sender}', content='{self.content[:20]}...')"


class File:
    """File domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        user_id: Optional[int] = None,
        file_name: str = "",
        file_url: str = "",
        file_type: str = "",
        status: Optional[str] = None,
        error_message: Optional[str] = None,
        created_at: Optional[datetime] = None
    ):
        self.id = id
        self.user_id = user_id
        self.file_name = file_name
        self.file_url = file_url
        self.file_type = file_type
        self.status = status
        self.error_message = error_message
        self.created_at = created_at or datetime.utcnow()
        self.segments = []
        self.chunks = []
        self.key_concepts = []
        self.flashcards = []
        self.quiz_questions = []
    
    def __repr__(self) -> str:
        return f"File(id={self.id}, file_name='{self.file_name}', file_type='{self.file_type}')"


class Segment:
    """Segment domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        file_id: Optional[int] = None,
        page_number: Optional[int] = None,
        content: str = "",
        meta_data: Optional[Dict[str, Any]] = None
    ):
        self.id = id
        self.file_id = file_id
        self.page_number = page_number
        self.content = content
        self.meta_data = meta_data or {}
        self.chunks = []
    
    def __repr__(self) -> str:
        return f"Segment(id={self.id}, page_number={self.page_number}, content='{self.content[:20]}...')"


class Chunk:
    """Chunk domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        file_id: Optional[int] = None,
        segment_id: Optional[int] = None,
        content: str = "",
        embedding: Optional[List[float]] = None
    ):
        self.id = id
        self.file_id = file_id
        self.segment_id = segment_id
        self.content = content
        self.embedding = embedding
    
    def __repr__(self) -> str:
        return f"Chunk(id={self.id}, content='{self.content[:20]}...')"


class KeyConcept:
    """Key concept domain model."""
    def __init__(
        self,
        id: Optional[int] = None,
        file_id: Optional[int] = None,
        concept: str = "",
        explanation: str = "",
        span_text: Optional[str] = None,
        span_start: Optional[int] = None,
        span_end: Optional[int] = None,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ):
        self.id = id
        self.file_id = file_id
        self.concept = concept
        self.explanation = explanation
        self.span_text = span_text
        self.span_start = span_start
        self.span_end = span_end
        self.source_page_number = source_page_number
        self.source_video_timestamp_start_seconds = source_video_timestamp_start_seconds
        self.source_video_timestamp_end_seconds = source_video_timestamp_end_seconds
        self.flashcards = []
        self.quiz_questions = []
    
    def __repr__(self) -> str:
        return f"KeyConcept(id={self.id}, concept='{self.concept}')"


# Flashcard domain model removed as it's no longer in the DB schema


# QuizQuestion domain model removed as it's no longer in the DB schema
