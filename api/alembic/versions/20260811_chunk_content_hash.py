"""Stop paying to embed text we have already embedded.

WHY

Embedding is the one part of ingestion that costs money per call, and the same
text goes through it repeatedly: a customer re-uploads the corrected version of
a policy, the same standard terms appear in twenty contracts, a form's header
page repeats in every submission. Each of those is billed again today, and the
answer is always the same vector, because embedding is a pure function of its
input.

WHAT THE HASH IS OVER

The text that is actually sent to the embedder, which is `embedding_text()`:
the chunk's own passage, prefixed with its context sentence where one exists.
Not the chunk's `content` column alone. Two chunks can hold identical passages
and be embedded differently because their context says they are from different
sections, and a cache that ignored that would hand back the wrong vector.

WHY ON chunks RATHER THAN A CACHE TABLE

The vectors are already here. A separate table would be a second copy of every
embedding, growing without bound, needing its own eviction, and able to drift
from the chunks it is supposed to describe. A column plus an index reuses what
exists and disappears with the document when it is deleted.

THE BACKFILL IS NOT EXACT, DELIBERATELY

Existing rows are hashed from `content` alone, because the context they were
embedded with is not recoverable from the chunk. Where a chunk had no context
that hash is exactly right. Where it had one the hash will not match anything
the application produces, so that row is simply never reused. A miss costs one
embedding call, which is what happens today; a wrong hit would cost a wrong
answer, so the failure is on the safe side. `CONTEXTUALIZE_CHUNKS` has been off,
so in practice almost every existing row backfills correctly.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260811_chunk_content_hash"
down_revision = "20260808_message_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("content_hash", sa.String(length=64), nullable=True))

    # sha256 over the exact bytes, hex encoded, matching hashlib in
    # base_processor. Built into Postgres since 11, so no pgcrypto extension.
    op.execute(
        """
        UPDATE chunks
        SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex')
        WHERE content IS NOT NULL AND content_hash IS NULL
        """
    )

    # The lookup is "have we embedded this exact text for this organization",
    # which reaches the organization by joining chunks to files to workspaces.
    # This index carries the hash side of that join; the file and workspace
    # sides are already indexed.
    op.create_index("idx_chunks_content_hash", "chunks", ["content_hash"])


def downgrade() -> None:
    op.drop_index("idx_chunks_content_hash", table_name="chunks")
    op.drop_column("chunks", "content_hash")
