"""Let a customer say an answer was wrong, and let us find out why.

WHY

Every claim about answer quality is currently our own assertion. The benchmark
says 18-19 of 27, but it is 27 questions we wrote about documents we chose. What
a dental practice actually asks its own insurance PDFs is unknown, and so is
which of those answers were wrong.

A rating alone would only produce a number. The useful part is the join: what
was retrieved, whether coverage was satisfied, how much context the model was
given. agent_runs already records all of that for every query, in `result`
alongside the question in `payload`.

It could not be reached from a message. agent_runs carries chat_history_id but
not message_id, so tying a rating to the run that produced it meant matching on
timestamps within a conversation, which is a guess. Hence the second half of
this migration: one nullable column that makes the join exact.

WHY A SEPARATE TABLE RATHER THAN COLUMNS ON messages

messages is read in full on every conversation load. Feedback is sparse, and it
carries a reason, a comment and a run reference that have no business widening
that read. Keeping it separate also means the unique constraint below expresses
the rule directly.

WHAT THE UNIQUE CONSTRAINT IS FOR

One rating per person per message, so pressing thumbs-down and then thumbs-up
replaces rather than accumulating, and a double-click cannot produce two rows
that disagree. This is load-bearing, not decoration.

NO BACKFILL

Existing agent_runs get message_id NULL and keep it. The link did not exist when
they ran and inventing one from timestamps is exactly the guess this column
exists to avoid. Feedback on an older message simply records no run.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260808_message_feedback"
down_revision = "20260807_username_not_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        # The answer being rated. Cascade: a deleted conversation should not
        # leave ratings pointing at messages that are gone.
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # -1 or 1 only. A check constraint rather than an enum: two values that
        # will not grow, and an enum type is a migration to change.
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        # A chip, not free text: 'wrong', 'incomplete', 'not_in_documents',
        # 'wrong_source'. Kept as a string so adding a chip is a frontend
        # change, not a migration.
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        # Which run produced the answer. SET NULL rather than CASCADE: if runs
        # are ever pruned, the rating is still worth keeping.
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating IN (-1, 1)", name="ck_message_feedback_rating"),
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
    )
    # The report reads "every thumbs-down, newest first".
    op.create_index(
        "idx_message_feedback_rating_created",
        "message_feedback",
        ["rating", "created_at"],
    )
    op.create_index("idx_message_feedback_user_id", "message_feedback", ["user_id"])

    # The join that makes a rating diagnostic instead of a tally.
    op.add_column(
        "agent_runs",
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("idx_agent_runs_message_id", "agent_runs", ["message_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_runs_message_id", table_name="agent_runs")
    op.drop_column("agent_runs", "message_id")
    op.drop_index("idx_message_feedback_user_id", table_name="message_feedback")
    op.drop_index("idx_message_feedback_rating_created", table_name="message_feedback")
    op.drop_table("message_feedback")
