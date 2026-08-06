"""Keep the table of contents that extraction was throwing away.

Every PDF is opened with fitz at upload, read page by page for its text, and
closed again without anyone asking what it contains. Three of the five
documents in the benchmark corpus carry a real embedded table of contents, 118
entries in one of them, and all of it was discarded.

That absence is what made the ADA failure unfixable by tuning: asked whether a
shop must be wheelchair accessible, retrieval returned page 8, which mentions
"readily achievable" while explaining parking, instead of page 6, where the
rule is defined. Both pages contain the phrase and no ranking function can tell
which one is the section about it. A contents page can.

JSON rather than a table because it is read whole, per document, and never
queried across documents. A row per heading would buy nothing and cost a join.

Revision ID: 20260805_file_outline
Revises: 20260804_segment_ctx
"""
from alembic import op


revision = "20260805_file_outline"
down_revision = "20260804_segment_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS outline jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS outline")
