"""Drafts SyntextAI wrote, kept structurally out of the retrieval corpus.

WHY ITS OWN TABLE

A generated draft must not answer questions until a person has approved it. If
it could, the model's own output becomes the model's own source of truth: it
writes a plausible SOP with one wrong figure, that gets ingested, and from then
on it cites itself with a page reference indistinguishable from a real one. The
customer has no way to tell.

That could have been a boolean on `files`, and a boolean is something a future
query forgets to check. Retrieval joins `files`; it does not join this table and
cannot. A draft is unretrievable by construction rather than by remembering.

Approving one does not move a row. It writes the bytes to storage and creates an
ordinary `files` row queued for ingestion, which is the same path an upload
takes. An approval is another way in, not a way around.

Revision ID: 20260826_generated_documents
Revises: 20260826_document_currency
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '20260826_generated_documents'
down_revision = '20260826_document_currency'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'generated_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        # The tenant boundary. Drafts carry no organization of their own and
        # reach it through the workspace, exactly like files.
        sa.Column('workspace_id', sa.Integer(),
                  sa.ForeignKey('workspaces.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        # Cleared when the account goes. A draft belongs to the workspace, not
        # to whoever happened to ask for it: cascading here would mean
        # offboarding somebody destroyed the company's own drafts.
        sa.Column('created_by', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        # What was asked for. Kept so a draft can be regenerated, and so the
        # customer can see what produced it.
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        # The pages this was drawn from: file name, page number, file id. Enough
        # to show provenance without re-running retrieval.
        sa.Column('sources', JSONB(), nullable=True),
        # 'draft' or 'ingested'. Never consulted by retrieval, which cannot see
        # this table at all; it drives what the UI offers.
        sa.Column('status', sa.String(), nullable=False, server_default='draft'),
        # The files row created when somebody approved this. SET NULL so
        # deleting that document leaves the draft intact rather than removing
        # the record of what was written.
        sa.Column('ingested_file_id', sa.Integer(),
                  sa.ForeignKey('files.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # The list is always "drafts in this workspace, newest first".
    op.create_index('idx_generated_documents_workspace_created',
                    'generated_documents', ['workspace_id', 'created_at'])


def downgrade():
    op.drop_index('idx_generated_documents_workspace_created',
                  table_name='generated_documents')
    op.drop_table('generated_documents')
