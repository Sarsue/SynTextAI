"""
ORM models for database tables.
This file contains SQLAlchemy ORM models extracted from the original docsynth_store.py.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Float, Boolean, UniqueConstraint, TIMESTAMP, text, Enum, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
from typing import List, Optional
import uuid
from sqlalchemy.dialects.postgresql import UUID, JSONB

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
    workspaces = relationship("Workspace", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    """The tenant: the billing entity and the security boundary.

    Everything a company owns hangs off this. Subscriptions attach here rather
    than to a person, so an invited colleague is a member of the organization
    instead of reading as a separate customer.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    # Uses the product without paying: demo and evaluation accounts. Entitled
    # without a subscription, and never charged for seats.
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=datetime.utcnow)

    # passive_deletes lets Postgres' ON DELETE CASCADE do the work. Without it
    # SQLAlchemy tries to NULL the child foreign keys first, which fails against
    # the NOT NULL constraint on workspaces.organization_id and silently aborts
    # the whole delete.
    members = relationship(
        "OrganizationMember",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    workspaces = relationship(
        "Workspace",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    subscriptions = relationship(
        "Subscription",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OrganizationMember(Base):
    """Recorded membership of a user in an organization.

    This replaces guessing whether someone is a mere invitee by counting the
    workspaces they own. Membership is a fact stored here, with an explicit role.
    """
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),
        # Named as the migration created it: index=True would have generated
        # ix_organization_members_organization_id and proposed swapping them.
        Index("ix_organization_members_org_id", "organization_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # owner: billing plus everything. admin: manage members, no billing.
    # member: use it.
    role = Column(String, nullable=False, default="staff")
    # How far that membership reaches. 'organization' sees every workspace in
    # the tenant; 'workspace' sees only the ones they were added to. Owners and
    # admins administer the tenant, so this does not narrow them.
    scope = Column(String, nullable=False, server_default=text("'workspace'"))
    joined_at = Column(DateTime, nullable=False, server_default=func.now())

    organization = relationship("Organization", back_populates="members")
    user = relationship("User")


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    # Retained as "who created it". Authorization comes from the organization,
    # not from this column.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="workspaces")
    organization = relationship("Organization", back_populates="workspaces")
    files = relationship("File", back_populates="workspace", cascade="all, delete-orphan")
    members = relationship("WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan")
    invites = relationship("WorkspaceInvite", back_populates="workspace", cascade="all, delete-orphan")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        Index("ix_workspace_members_workspace_id", "workspace_id"),
        Index("ix_workspace_members_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False, default="staff")  # "owner" | "staff"
    joined_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="members")
    user = relationship("User")


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"
    # Uniqueness on token is enforced by a named constraint plus a separate
    # non-unique index, which is what the migration built. Declaring
    # unique=True, index=True on the column instead collapses both into one
    # unique index, so autogenerate proposed dropping the constraint and
    # rebuilding the index.
    __table_args__ = (
        UniqueConstraint("token", name="workspace_invites_token_key"),
        Index("ix_workspace_invites_token", "token"),
        Index("ix_workspace_invites_workspace_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # NULL for an organization-wide invite, which names no single workspace.
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    email = Column(String, nullable=False)
    token = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # "pending" | "accepted" | "expired"
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    workspace = relationship("Workspace", back_populates="invites")

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
    # The organization is what actually pays. user_id is retained so existing
    # Stripe-customer lookups keep working during the transition.
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    # Seat allowance included in the plan before overage applies.
    seats = Column(Integer, nullable=True)
    # Which plan: seat pricing differs per plan, so "what would one more member
    # cost?" is unanswerable without it.
    plan_key = Column(String, nullable=True)

    user = relationship("User", back_populates="subscriptions")
    organization = relationship("Organization", back_populates="subscriptions")
    
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
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    file_type = Column(String, nullable=True)  # 'pdf', 'youtube', etc.
    # Using direct PostgreSQL enum type with string literal
    processing_status = Column(
        String,  # Using String instead of Enum type to avoid case sensitivity issues
        default="uploaded",
        nullable=False,
        index=True
    )
    file_size_bytes = Column(Integer, nullable=True)  # Size of the original source file in bytes
    
    # Relationships
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    user = relationship("User", back_populates="files")
    workspace = relationship("Workspace", back_populates="files")

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

class ChatHistory(Base):
    __tablename__ = "chat_histories"
    # Declared here as well as in the migration, or autogenerate sees an index
    # the model does not know about and proposes dropping it.
    __table_args__ = (
        Index("ix_chat_histories_user_workspace", "user_id", "workspace_id"),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String, default="Untitled")
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    # A conversation belongs to the workspace whose documents it was answered
    # from. Without this, switching workspace changed the documents but not the
    # thread list, and continuing an old thread silently answered from a
    # different set of files.
    # No index=True: the composite ix_chat_histories_user_workspace created by
    # the migration already serves the (user_id, workspace_id) filter this is
    # read by, and declaring one here just makes the model disagree with the
    # schema.
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)

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


class AgentRun(Base):
    __tablename__ = "agent_runs"
    # Named to match what the migration actually created. Declaring these as
    # index=True on the columns instead produced ix_agent_runs_* in the model
    # against idx_agent_runs_* in the database, so autogenerate proposed
    # dropping four live indexes and recreating identical ones under new names.
    #
    # idx_agent_runs_queue is the one that matters: the worker's poll filters
    # and orders on exactly these four columns and runs continuously, so losing
    # it turns every poll into a sequential scan. It was invisible to the model
    # entirely, which meant autogenerate wanted it gone with no replacement.
    __table_args__ = (
        Index("idx_agent_runs_queue", "status", "priority", "run_after", "created_at"),
        Index("idx_agent_runs_user_id", "user_id"),
        Index("idx_agent_runs_file_id", "file_id"),
        Index("idx_agent_runs_chat_history_id", "chat_history_id"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )

    run_type = Column(String, nullable=False)
    agent_name = Column(String, nullable=False)
    agent_version = Column(String, nullable=True)

    status = Column(String, nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=100)

    payload = Column(JSONB, nullable=False)
    result = Column(JSONB, nullable=True)

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    chat_history_id = Column(Integer, ForeignKey("chat_histories.id", ondelete="CASCADE"), nullable=True)

    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    last_error = Column(Text, nullable=True)

    locked_by = Column(String, nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    run_after = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "run_type": self.run_type,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "status": self.status,
            "priority": self.priority,
            "payload": self.payload,
            "result": self.result,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "file_id": self.file_id,
            "chat_history_id": self.chat_history_id,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "locked_by": self.locked_by,
            "locked_at": self.locked_at,
            "lease_expires_at": self.lease_expires_at,
            "run_after": self.run_after,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
