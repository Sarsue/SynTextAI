"""
ORM models for database tables.
This file contains SQLAlchemy ORM models extracted from the original docsynth_store.py.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Float, Boolean, Table, UniqueConstraint, TIMESTAMP
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
from typing import List, Optional

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
    stripe_customer_id = Column(String, nullable=False)
    stripe_subscription_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    current_period_end = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    trial_end = Column(DateTime, nullable=True)
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    user = relationship("User", back_populates="subscriptions")
    
    # Link to CardDetails
    card_details = relationship("CardDetails", back_populates="subscription", cascade="all, delete-orphan")

class CardDetails(Base):
    __tablename__ = "card_details"
    
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)  # Link to the Subscription table
    card_last4 = Column(String(4), nullable=False)  # Last 4 digits of the card
    card_type = Column(String(50), nullable=False)  # Card type (e.g., Visa, Mastercard)
    exp_month = Column(Integer, nullable=False)  # Expiration month
    exp_year = Column(Integer, nullable=False)  # Expiration year
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    subscription = relationship("Subscription", back_populates="card_details")

class File(Base):
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    file_name = Column(String)
    file_url = Column(String)
    created_at = Column(DateTime, default=func.now())
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Relationships
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    user = relationship("User", back_populates="files")
    key_concepts = relationship("KeyConcept", backref="file", cascade="all, delete-orphan", lazy="selectin")

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

# Other models like Flashcard and QuizQuestion are removed because they're not in the original schema

class KeyConcept(Base):
    __tablename__ = "key_concepts"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    
    concept_title = Column(String, nullable=False)
    concept_explanation = Column(Text, nullable=False)
    
    display_order = Column(Integer, nullable=True, default=0) 

    source_page_number = Column(Integer, nullable=True) 
    source_video_timestamp_start_seconds = Column(Integer, nullable=True)
    source_video_timestamp_end_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<KeyConcept(id={self.id}, file_id={self.file_id}, title='{self.concept_title[:30]}...')>"

class ChatHistory(Base):
    __tablename__ = "chat_histories"
    
    id = Column(Integer, primary_key=True)
    title = Column(String, default="Untitled")
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    
    # Relationships
    user = relationship("User", back_populates="chat_histories")
    messages = relationship("Message", back_populates="chat_history", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True)
    content = Column(Text)
    sender = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    chat_history_id = Column(Integer, ForeignKey("chat_histories.id", ondelete="CASCADE"))
    
    # Relationships
    user = relationship("User", back_populates="messages")
    chat_history = relationship("ChatHistory", back_populates="messages")
