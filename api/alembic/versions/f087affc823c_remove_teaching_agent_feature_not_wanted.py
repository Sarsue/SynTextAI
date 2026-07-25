"""remove teaching agent feature (not wanted)

Revision ID: f087affc823c
Revises: befb0c5f5d70
Create Date: 2026-07-24 10:41:16.630287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f087affc823c'
down_revision: Union[str, None] = 'befb0c5f5d70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('user_credits')
    op.drop_table('teaching_messages')
    op.drop_table('teaching_sessions')
    op.drop_table('user_topic_progress')
    op.drop_table('user_enrollments')
    op.drop_table('domain_files')
    op.drop_table('curriculum_topics')
    op.drop_table('domains')
    op.drop_column('files', 'is_platform_content')


def downgrade() -> None:
    op.add_column('files', sa.Column('is_platform_content', sa.Boolean(), nullable=False, server_default='false'))

    op.create_table(
        'domains',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon_url', sa.String(), nullable=True),
        sa.Column('difficulty', sa.String(), nullable=True),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_domains_slug', 'domains', ['slug'], unique=True)

    op.create_table(
        'curriculum_topics',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('domain_id', sa.Integer(), sa.ForeignKey('domains.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('prerequisite_topic_id', sa.Integer(), sa.ForeignKey('curriculum_topics.id', ondelete='SET NULL'), nullable=True),
        sa.Column('estimated_minutes', sa.Integer(), nullable=True, server_default='15'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_curriculum_topics_domain_id', 'curriculum_topics', ['domain_id'])

    op.create_table(
        'domain_files',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('domain_id', sa.Integer(), sa.ForeignKey('domains.id', ondelete='CASCADE'), nullable=False),
        sa.Column('topic_id', sa.Integer(), sa.ForeignKey('curriculum_topics.id', ondelete='CASCADE'), nullable=True),
        sa.Column('file_id', sa.Integer(), sa.ForeignKey('files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_platform_content', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_domain_files_domain_id', 'domain_files', ['domain_id'])
    op.create_index('ix_domain_files_topic_id', 'domain_files', ['topic_id'])

    op.create_table(
        'user_enrollments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('domain_id', sa.Integer(), sa.ForeignKey('domains.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enrolled_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'domain_id', name='uq_user_domain_enrollment'),
    )
    op.create_index('ix_user_enrollments_user_id', 'user_enrollments', ['user_id'])
    op.create_index('ix_user_enrollments_domain_id', 'user_enrollments', ['domain_id'])

    op.create_table(
        'user_topic_progress',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('topic_id', sa.Integer(), sa.ForeignKey('curriculum_topics.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mastery_score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('sessions_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_reviewed_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'topic_id', name='uq_user_topic_progress'),
    )
    op.create_index('ix_user_topic_progress_user_id', 'user_topic_progress', ['user_id'])
    op.create_index('ix_user_topic_progress_topic_id', 'user_topic_progress', ['topic_id'])

    op.create_table(
        'teaching_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('topic_id', sa.Integer(), sa.ForeignKey('curriculum_topics.id', ondelete='SET NULL'), nullable=True),
        sa.Column('file_id', sa.Integer(), sa.ForeignKey('files.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('session_state', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('credits_used', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_teaching_sessions_user_id', 'teaching_sessions', ['user_id'])
    op.create_index('ix_teaching_sessions_topic_id', 'teaching_sessions', ['topic_id'])

    op.create_table(
        'teaching_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('teaching_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('phase', sa.String(), nullable=True),
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_teaching_messages_session_id', 'teaching_messages', ['session_id'])

    op.create_table(
        'user_credits',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('balance', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('total_purchased', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_user_credits_user_id', 'user_credits', ['user_id'])
