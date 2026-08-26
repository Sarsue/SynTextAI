"""Document currency: a link to the document that replaced this one.

A workspace holding both the 2019 policy and the 2024 policy that replaced it
ranked them identically, because `files` carried only `created_at`, which is
when somebody uploaded a file and not which document is true. Either could be
cited, and the customer's own feedback ("cited the 2019 policy, we are on the
2024 one") was the only thing that ever noticed.

An `effective_date` column was added here first and removed before this shipped.
Nothing set it, nothing displayed it, and retrieval did not read it: a date that
looks like it decides which document answers, and does not, is worse than no
date. The supersede link answers "which one is true" definitively and is
enforced in the query. If a customer asks to see effective dates, that is a
column plus a UI plus a decision about ranking, and it starts from measurement.

The link is stored on the OLD file pointing forward, rather than on the new file
pointing back. Retrieval asks "is this superseded?" about every candidate row it
has already joined `files` for, so forward-pointing makes that a column test
(`f.superseded_by_id IS NULL`) folded into the existing WHERE. Backward-pointing
would make it a NOT EXISTS subquery against `files` per candidate, on the hot
path, for the same answer.

ON DELETE SET NULL, so deleting the newer document brings the older one back
into answers rather than hiding it forever with nothing pointing at it.

Revision ID: 20260826_document_currency
Revises: 20260815_drop_unread
"""
from alembic import op
import sqlalchemy as sa

revision = '20260826_document_currency'
down_revision = '20260815_drop_unread'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('files', sa.Column('superseded_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_files_superseded_by_id',
        'files', 'files',
        ['superseded_by_id'], ['id'],
        ondelete='SET NULL',
    )
    # Retrieval filters on this column on every search, and the reverse lookup
    # ("what did this document replace?") reads it too.
    op.create_index('idx_files_superseded_by_id', 'files', ['superseded_by_id'])


def downgrade():
    op.drop_index('idx_files_superseded_by_id', table_name='files')
    op.drop_constraint('fk_files_superseded_by_id', 'files', type_='foreignkey')
    op.drop_column('files', 'superseded_by_id')
