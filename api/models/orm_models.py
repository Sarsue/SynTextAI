"""
ORM models for database tables.
This file contains SQLAlchemy ORM models extracted from the original docsynth_store.py.
"""
from sqlalchemy import Column, Integer, SmallInteger, String, DateTime, ForeignKey, Text, JSON, Float, Boolean, CheckConstraint, UniqueConstraint, TIMESTAMP, text, Enum, Index
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
    # Identity is the email. This is the display name off the sign-in token
    # ("John Smith"), shown to humans and never used to look anybody up, so it
    # must not be unique: it was, and the second John Smith to sign up got a
    # 500 and no account. See migration 20260807_username_not_unique.
    username = Column(String, nullable=False)
    
    # Relationships
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    chat_histories = relationship("ChatHistory", back_populates="user", cascade="all, delete-orphan")
    # No delete cascade, and passive_deletes so Postgres' ON DELETE SET NULL is
    # what happens. With the ORM cascade in place SQLAlchemy deleted the File
    # rows itself before the database ever saw the delete, so a document
    # uploaded into a company workspace still vanished when its uploader was
    # removed — the schema said one thing and the mapping did another.
    files = relationship("File", back_populates="user", passive_deletes=True)
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
        # One person owns at most one company. Declared here, not only in the
        # migration, because autogenerate compares the database against these
        # models: undeclared, the next generated migration proposes dropping it,
        # and dropping it silently restores the race that gave one email two
        # companies from a single click.
        #
        # Partial on purpose. Owning one while belonging to several is the whole
        # membership model, so the rule constrains owners and says nothing about
        # anybody else.
        Index(
            "uq_one_owned_organization_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
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
    # What they will be once they accept, decided by whoever invited them.
    # Every invite used to produce an organization-wide staff member, so the
    # owner had to correct the role and reach afterwards, on another screen,
    # once the person was already in.
    role = Column(String, nullable=False, default="staff")  # "staff" | "admin"
    scope = Column(String, nullable=False, default="organization")  # "organization" | "workspace"
    # The workspaces a 'workspace'-scoped invite grants. A list, because access
    # is a set: three of five workspaces is a normal thing to want, and the
    # single workspace_id above cannot say it.
    workspace_ids = Column(JSON, nullable=True)
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
    # Who uploaded it, cleared when that account goes. A document belongs to
    # the workspace, not to the person who happened to add it: cascading here
    # meant offboarding somebody destroyed the company's own documents.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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

    # The document that replaced this one. Set on the OLD file, pointing
    # forward. Retrieval has already joined `files` for every candidate chunk,
    # so forward-pointing makes "is this still current?" a column test folded
    # into the existing WHERE; backward-pointing would be a NOT EXISTS subquery
    # per candidate on the hot path for the same answer.
    #
    # SET NULL rather than CASCADE: deleting the replacement should bring the
    # older document back into answers, not leave it hidden with nothing
    # pointing at it.
    superseded_by_id = Column(
        Integer,
        ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    user = relationship("User", back_populates="files")
    workspace = relationship("Workspace", back_populates="files")

class Segment(Base):
    __tablename__ = "segments"
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    page_number = Column(Integer)  # This represents the page number within the file
    # The page as extracted. This is what a citation opens, so it is the whole
    # page and not the chunks rejoined, which would repeat their overlap.
    #
    # A segment holds no context sentence and no tsvector of its own any more.
    # Both moved to the chunk, which is the unit all three arms of hybrid_search
    # actually rank. See migration 20260815_drop_unread.
    content = Column(String)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    meta_data = Column(JSON, nullable=True) 
    
    # Relationship to file
    file = relationship("File", back_populates="segments")
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="segment", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Segment(id={self.id}, file_id={self.file_id}, page_number={self.page_number}, content={self.content[:50]}...)>"

class PageRead(Base):
    """One page as extraction produced it, kept so a crash does not re-buy it.

    A vision-read page costs roughly 150 to 200 seconds against a metered
    endpoint. Extraction completes in full before the first batch of chunks is
    written, so without this an interrupted ingest threw away every page it had
    already paid for. See alembic/versions/20260812_page_reads.py.

    Cache, not record. Safe to delete; costs money, not correctness.
    """
    __tablename__ = "page_reads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    # "vision", "text" or "ocr".
    source = Column(String(16), nullable=False)
    flags = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("file_id", "page_number", name="idx_page_reads_file_page"),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"))
    segment_id = Column(Integer, ForeignKey("segments.id", ondelete="CASCADE"))  # Link to the segment
    
    # Vector embedding for each chunk
    embedding = Column(Vector(1024), nullable=True)  # Example size (e.g., 1536 for OpenAI embeddings)

    # The retrieval unit's own text. A chunk is a slice of a page; the page's
    # full text stays on the segment, because a citation names a page and a page
    # is what a reader opens.
    content = Column(Text, nullable=True)

    # sha256 of the text that was embedded, which is `content`. Lets the same
    # text be embedded once and reused, so a re-uploaded document is not billed
    # twice. See migration 20260811_chunk_content_hash.
    #
    # That migration describes the hash as covering a context sentence in front
    # of the passage. It no longer does: the contextualiser was measured and
    # removed on 2026-08-15, so the hash is over the chunk's text alone.
    #
    # Declared without index=True: the migration creates idx_chunks_content_hash
    # under that name, and declaring one here too makes autogenerate propose
    # dropping the live index and adding an identical one called ix_*.
    content_hash = Column(String(64), nullable=True)

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
    feedback = relationship(
        "MessageFeedback", back_populates="message", cascade="all, delete-orphan"
    )


class GeneratedDocument(Base):
    """A draft SyntextAI wrote, which cannot answer questions until approved.

    Deliberately NOT a `files` row with a flag. If a generated draft could be
    retrieved, the model's own output becomes its own source of truth: it writes
    a plausible SOP with one wrong figure, that gets ingested, and afterwards it
    cites itself with a page reference indistinguishable from a real one. A
    boolean is something a future query forgets to check. Retrieval joins
    `files`; it does not join this table and cannot, so a draft is unretrievable
    by construction.

    Approving one does not move a row. It writes the bytes to storage and
    creates an ordinary `files` row queued for ingestion, the same path an
    upload takes.
    """
    __tablename__ = "generated_documents"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # A draft belongs to the workspace, not to whoever asked for it. Cascading
    # here would mean offboarding somebody destroyed the company's drafts.
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title = Column(String, nullable=False)
    # Kept so a draft can be regenerated, and so the customer can see what
    # produced it.
    prompt = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    # The pages this drew on: file name, page number, file id.
    sources = Column(JSONB, nullable=True)
    # 'draft' or 'ingested'. Drives what the UI offers. Retrieval never reads
    # it, because retrieval cannot see this table.
    status = Column(String, nullable=False, default="draft")
    ingested_file_id = Column(
        Integer, ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MessageFeedback(Base):
    """What a customer thought of one answer.

    Separate from messages because messages is read in full on every
    conversation load, and this carries a reason and a comment that have no
    business widening that read. See migration 20260808_message_feedback.
    """
    __tablename__ = "message_feedback"
    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="ck_message_feedback_rating"),
        # One rating per person per message: pressing the other thumb replaces
        # rather than accumulating, and a double click cannot leave two rows
        # that disagree.
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
        Index("idx_message_feedback_rating_created", "rating", "created_at"),
        Index("idx_message_feedback_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True)
    message_id = Column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating = Column(SmallInteger, nullable=False)
    reason = Column(String(32), nullable=True)
    comment = Column(Text, nullable=True)
    # No agent_run_id here on purpose. The run that produced the rated answer is
    # reachable through agent_runs.message_id, so holding a copy would duplicate
    # a fact rather than add one, and two copies are what drift.
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    message = relationship("Message", back_populates="feedback")


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
    # The answer this run produced, so a rating on that message can be read
    # next to what was retrieved. Nullable and never backfilled: runs from
    # before 20260808_message_feedback have no message to point at.
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)

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
