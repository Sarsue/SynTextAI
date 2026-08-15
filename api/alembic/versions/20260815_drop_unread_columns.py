"""Drop three columns nothing reads: files.outline, segments.context, segments.tsv.

Each was written deliberately and each lost its reader. Keeping a column that
only ever gets written is worse than not having it: every future reader has to
work out whether it is authoritative, and `segments.tsv` in particular is a GIN
index maintained on every insert to answer a query nobody sends.

FILES.OUTLINE

A document's table of contents, taken at upload. It had two consumers and both
are gone, for reasons recorded at the time:

  - the `outline` tool in `document_tools.py`. Its own comment, written on
    2026-08-05: "The `outline` tool exists and the model does not call it,
    exactly as it did not call read_page when told to. Two prompt revisions
    failed to change that."
  - `workspace_map`, which stopped asking the model to look and stuffed every
    document's contents into the prompt instead. It went with the whole tool
    layer in 40ca829, which lost to the single pipeline.

So this is not an untried idea. It is a tried one whose result was recorded.
Extraction also costs a full `get_text("dict")` pass over every page of any PDF
without an embedded contents page, paid on every upload for nobody.

To bring it back: `git revert` this migration and restore `api/services/outline.py`,
`api/scripts/backfill_outline.py` and the call in `pdf_processor.process`, all
of which are in the history at c026c2d.

SEGMENTS.CONTEXT AND SEGMENTS.TSV

The context sentence is going entirely, not moving. See the contextual
retrieval section of docs/ENGINEERING_OVERVIEW.md: measured on a corpus whose
vectors were correct and with the keyword half connected for the first time, it
retrieved no more than plain chunks and ranked slightly worse, so the whole
feature was removed on 2026-08-15.

`segments.tsv` has not been queried by anything since chunk-level retrieval
landed on 2026-08-06. All three arms of `hybrid_search` rank `chunks.tsv`.

The segment keeps `content`, which is the page a citation opens. That is what a
segment is for.

Revision ID: 20260815_drop_unread
Revises: 20260812_page_reads
"""
from alembic import op


revision = "20260815_drop_unread"
down_revision = "20260812_page_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS outline")
    op.execute("DROP INDEX IF EXISTS ix_segments_tsv")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS context")


def downgrade() -> None:
    op.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS outline jsonb")
    op.execute("ALTER TABLE segments ADD COLUMN IF NOT EXISTS context text")
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
