"""Store the context written for each chunk, and index it with the text.

See services/contextualizer.py for what this holds and why. In short: a chunk
embedded in isolation carries only the words on its own page, so page 8 of the
ADA guide says "if doing so is readily achievable" while explaining parking and
nothing in it says which rule that is or which document it belongs to.

Its own column rather than mixed into content, because content is what a
customer reads when they follow a citation and it has to be what the document
actually says.

The tsvector is rebuilt to cover both. Anthropic's numbers make this the
difference between a 35% and a 49% reduction in retrieval failures: contextual
embeddings alone leave the keyword half of hybrid search still blind to it.
Weighting: 'A' for the context, 'B' for the body, so a match on the sentence
that names the document's subject counts for more than a passing mention.

Revision ID: 20260804_segment_ctx
Revises: 20260804_retrieval_idx
"""
from alembic import op


revision = "20260804_segment_ctx"
down_revision = "20260804_retrieval_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE segments ADD COLUMN IF NOT EXISTS context text")

    # A generated column cannot be altered in place; drop and rebuild it.
    op.execute("DROP INDEX IF EXISTS ix_segments_tsv")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS tsv")
    op.execute(
        """
        ALTER TABLE segments
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(context, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(content, '')), 'B')
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_segments_tsv ON segments USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_segments_tsv")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS tsv")
    op.execute(
        """
        ALTER TABLE segments
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
        """
    )
    op.execute("CREATE INDEX ix_segments_tsv ON segments USING gin (tsv)")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS context")
