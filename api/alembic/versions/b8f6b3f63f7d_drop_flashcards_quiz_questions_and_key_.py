"""drop flashcards, quiz_questions, and key_concepts tables

Revision ID: b8f6b3f63f7d
Revises: 960d7dae1a8a
Create Date: 2026-07-24 10:33:29.110058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f6b3f63f7d'
down_revision: Union[str, None] = 'f087affc823c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Children first (both reference key_concepts.id) to satisfy FK constraints.
    op.drop_table("flashcards")
    op.drop_table("quiz_questions")
    op.drop_table("key_concepts")


def downgrade() -> None:
    op.create_table(
        "key_concepts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("concept_title", sa.String(), nullable=False),
        sa.Column("concept_explanation", sa.Text(), nullable=False),
        sa.Column("source_page_number", sa.Integer(), nullable=True),
        sa.Column("source_video_timestamp_start_seconds", sa.Integer(), nullable=True),
        sa.Column("source_video_timestamp_end_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "flashcards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE")),
        sa.Column("key_concept_id", sa.Integer(), sa.ForeignKey("key_concepts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("question", sa.String()),
        sa.Column("answer", sa.String()),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.create_table(
        "quiz_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE")),
        sa.Column("key_concept_id", sa.Integer(), sa.ForeignKey("key_concepts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("question_type", sa.String(), nullable=False),
        sa.Column("correct_answer", sa.String(), nullable=False),
        sa.Column("distractors", sa.JSON(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
