"""Stop treating a person's display name as their identity.

WHY

users.username is not a username. Nobody chooses it and nobody types it. It is
whatever Google put in the `name` claim of the sign-in token, which is a display
name: "John Smith". It carried a UNIQUE constraint anyway.

So the second John Smith to ever sign up could not. add_user hit the constraint,
returned None, and POST /users turned that into 500 "Could not create user."
They saw a generic error, got no account, and no retry could help, because the
name comes from their Google profile and they cannot change it to suit us.
Silent, permanent for that person, and more likely with every signup.

Reproduced against the real table before writing this:

    add_user('alice.smith@…', 'John Smith')  -> 7395
    add_user('bob.smith@…',   'John Smith')  -> None

The constraint was protecting nothing. Identity here is the email address, which
has its own unique index (ix_users_email) and is what every lookup uses:
get_user_id_from_email is how a request becomes a user. username is read in
exactly one query in the whole codebase, async_workspace_repository's
"who would be stranded" list, as a label to show a human, already written as
COALESCE(NULLIF(u.username, ''), u.email) because it was never trusted to be
meaningful in the first place.

NOT NULL stays. The signup path substitutes the email when the token carries no
name, so the column is always populated, and a label is genuinely wanted.

DOWNGRADE

Recreating the constraint will fail if two people now share a display name,
which is the entire point of removing it. That is correct: there is no safe
automatic way back, and silently deleting one of two real accounts to satisfy a
cosmetic index would be far worse than a failed downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260807_username_not_unique"
down_revision = "20260806_chunk_retrieval"
branch_labels = None
depends_on = None


# Named rather than reflected. Postgres generated this name when the column was
# declared unique in the initial migration (85de474e33fd), and naming it here
# means the drop either works or fails loudly, instead of quietly matching
# nothing on a database whose constraint is called something else.
CONSTRAINT_NAME = "users_username_key"


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = :name
              AND conrelid = 'users'::regclass
            """
        ),
        {"name": CONSTRAINT_NAME},
    ).scalar()

    if exists:
        op.drop_constraint(CONSTRAINT_NAME, "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(CONSTRAINT_NAME, "users", ["username"])
