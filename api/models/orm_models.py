"""
ORM models for database tables.
This file contains SQLAlchemy ORM models extracted from the original docsynth_store.py.
"""
from enum import Enum as PyEnum
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
    Boolean,
    text,
    CheckConstraint,
    Enum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
from typing import List, Optional, TypeVar, Type, Any, Dict


class SubscriptionStatus(str, PyEnum):
    """Enum for subscription status values."""
    ACTIVE = "active"
    TRIALING = "trialing"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Check if a status string is a valid subscription status."""
        try:
            cls(status)
            return True
        except ValueError:
            return False

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    username = Column(String, nullable=False, unique=True)
    
    # Relationships
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    chat_histories = relationship("ChatHistory", back_populates="user", cascade="all, delete-orphan")
    files = relationship("File", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(Integer, primary_key=True)
    stripe_customer_id = Column(String, nullable=False, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)
    status = Column(Enum(SubscriptionStatus), nullable=False)
    current_period_end = Column(DateTime, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    trial_end = Column(DateTime, nullable=True)
    
    # Add indexes for frequently queried fields
    __table_args__ = (
        # Index for looking up active subscriptions
        {'sqlite_autoincrement': True},
    )
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user = relationship("User", back_populates="subscriptions")
    
    # Link to CardDetails
    card_details = relationship(
        "CardDetails", 
        back_populates="subscription", 
        uselist=False,  # One-to-one relationship
        cascade="all, delete-orphan"
    )
    
    def to_dict(self) -> dict:
        """Convert subscription to dictionary with all relevant fields."""
        result = {
            'id': self.id,
            'stripe_customer_id': self.stripe_customer_id,
            'stripe_subscription_id': self.stripe_subscription_id,
            'status': self.status.value if self.status else None,
            'current_period_end': self.current_period_end,
            'trial_end': self.trial_end,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'user_id': self.user_id,
        }
        
        # Add card details if they exist
        if self.card_details:
            result.update({
                'card_last4': self.card_details.card_last4,
                'card_brand': self.card_details.card_type,
                'card_exp_month': self.card_details.exp_month,
                'card_exp_year': self.card_details.exp_year,
            })
            
        return result

class CardDetails(Base):
    __tablename__ = "card_details"
    
    id = Column(Integer, primary_key=True)
    subscription_id = Column(
        Integer, 
        ForeignKey("subscriptions.id", ondelete="CASCADE"), 
        nullable=False,
        unique=True  # Ensure one-to-one relationship
    )
    card_last4 = Column(String(4), nullable=False)  # Last 4 digits of the card
    card_type = Column(String(50), nullable=False)  # Card type (e.g., Visa, Mastercard)
    exp_month = Column(Integer, nullable=False)  # Expiration month (1-12)
    exp_year = Column(Integer, nullable=False)   # Expiration year (4 digits)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship
    subscription = relationship("Subscription", back_populates="card_details")
    
    def to_dict(self) -> dict:
        """Convert card details to dictionary."""
        return {
            'id': self.id,
            'subscription_id': self.subscription_id,
            'card_last4': self.card_last4,
            'card_type': self.card_type,
            'exp_month': self.exp_month,
            'exp_year': self.exp_year,
            'created_at': self.created_at
        }

class File(Base):
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    file_name = Column(String)
    file_url = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_type = Column(String, nullable=True)  # 'pdf', 'youtube', etc.
    processing_status = Column(
        String,
        nullable=False,
        index=True
    )
    
    # Relationships
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    user = relationship("User", back_populates="files")
    key_concepts = relationship("KeyConcept", backref="file", cascade="all, delete-orphan", lazy="selectin")
    flashcards = relationship("Flashcard", back_populates="file", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion", back_populates="file", cascade="all, delete-orphan")

class Segment(Base):
    __tablename__ = "segments"
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    page_number = Column(Integer)  # This represents the page number within the file
    content = Column(String)  # Content of the segment/page (optional, or could be derived from chunks)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    meta_data = Column(JSON, nullable=True) 
    
    # Relationship to file
    file = relationship("File", back_populates="segments")
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="segment", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Segment(id={self.id}, file_id={self.file_id}, page_number={self.page_number}, content={self.content[:50]}...)>"

class Chunk(Base):
    __tablename__ = "chunks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    segment_id = Column(Integer, ForeignKey("segments.id", ondelete="CASCADE"))  # Link to the segment
    
    # Vector embedding for each chunk
    embedding = Column(Vector(1024), nullable=True)  # Example size (e.g., 1536 for OpenAI embeddings)
    
    # Relationship to file
    file = relationship("File", back_populates="chunks")
    
    # Relationship to segment
    segment = relationship("Segment", back_populates="chunks")
    
    def __repr__(self):
        return f"<Chunk(id={self.id}, file_id={self.file_id}, segment_id={self.segment_id}, embedding={self.embedding[:50]}...)>"

class KeyConcept(Base):
    __tablename__ = "key_concepts"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    
    concept_title = Column(String, nullable=False)
    concept_explanation = Column(Text, nullable=False)
    
    source_page_number = Column(Integer, nullable=True) 
    source_video_timestamp_start_seconds = Column(Integer, nullable=True)
    source_video_timestamp_end_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_custom = Column(Boolean, default=False, nullable=False)
    
    # Relationships
    flashcards = relationship("Flashcard", back_populates="key_concept", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion", back_populates="key_concept", cascade="all, delete-orphan")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<KeyConcept(id={self.id}, file_id={self.file_id}, title='{self.concept_title[:30]}...')>"

class ChatHistory(Base):
    __tablename__ = "chat_histories"
    
    id = Column(Integer, primary_key=True)
    title = Column(String)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Relationships
    user = relationship("User", back_populates="chat_histories")
    messages = relationship("Message", back_populates="chat_history", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    content = Column(Text)
    sender = Column(String, nullable=False)  # 'user' or 'bot'
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    chat_history_id = Column(Integer, ForeignKey("chat_histories.id", ondelete="CASCADE"))
    
    # Relationships
    user = relationship("User", back_populates="messages")
    chat_history = relationship("ChatHistory", back_populates="messages")
    
    @property
    def role(self) -> str:
        """Get the role for LLM context (maps sender to role)."""
        return 'assistant' if self.sender == 'bot' else 'user'


class Flashcard(Base):
    __tablename__ = "flashcards"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    key_concept_id = Column(Integer, ForeignKey("key_concepts.id", ondelete="CASCADE"), nullable=True)
    question = Column(String)
    answer = Column(String)
    is_custom = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=True)
    
    # Relationships
    file = relationship("File", back_populates="flashcards")
    key_concept = relationship("KeyConcept", back_populates="flashcards")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    key_concept_id = Column(Integer, ForeignKey("key_concepts.id", ondelete="CASCADE"), nullable=True)
    question = Column(String, nullable=False)
    question_type = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)
    distractors = Column(JSON, nullable=True)
    is_custom = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=True)
    
    # Relationships
    file = relationship("File", back_populates="quiz_questions")
    key_concept = relationship("KeyConcept", back_populates="quiz_questions")

    def __repr__(self):
        return f"<QuizQuestion(id={self.id}, question='{self.question}')>"


# WebhookEvent model has been removed as webhook processing is now handled without database persistence
