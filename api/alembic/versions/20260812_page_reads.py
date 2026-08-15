"""Keep a page once we have paid to read it.

WHY

Extraction used to be fast enough that losing it did not matter. A crash meant
re-parsing a PDF, which is seconds. Vision extraction changed that: a page that
needs the vision model takes roughly 150 to 200 seconds, and a 69-page manual
with 58 digit-dense pages is about an hour of metered calls.

None of that hour was durable. `embed_and_store_pages` writes in batches and
resumes (2026-08-11), but every page is extracted before the first batch is
written, so a restart anywhere in the extraction phase discarded all of it and
began again at page one.

Measured 2026-08-12: the Carrier manual was extracted three times over 45
minutes and then failed, because the 15 minute lease expired while the worker
was very much alive. That is a separate bug and is fixed in worker.py, but it
made the cost of throwing away extraction impossible to ignore. Three hours of
vision calls bought nothing.

WHAT IS STORED

One row per page of a file, holding the text as extraction produced it and the
flags that came with it. Written as each page lands rather than at the end, so
an interrupted run keeps everything up to the interruption.

WHY NOT REDIS

Redis here is a notification bus and is allowed to be empty at any moment. This
is expensive work product, and the record lives in Postgres.

WHY NOT segments

`segments` is post-chunking output that carries context sentences and has
chunks hanging off it. This is the raw read, before any of that, and it needs to
exist at a point where none of the rest has happened yet.

LIFETIME

Cascades with the file. A row is a cache entry, not a source of truth: deleting
the whole table costs money and no correctness.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260812_page_reads"
down_revision = "20260811_chunk_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_reads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "file_id",
            sa.Integer(),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # "vision", "text" or "ocr". Which of the three produced this page, so a
        # later question about why a page reads the way it does has an answer,
        # and so a re-run can choose to distrust one source without dropping the
        # others.
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # The lookup is always "every page we already have for this file", and the
    # uniqueness is what makes a re-read idempotent.
    op.create_index(
        "idx_page_reads_file_page",
        "page_reads",
        ["file_id", "page_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_page_reads_file_page", table_name="page_reads")
    op.drop_table("page_reads")
