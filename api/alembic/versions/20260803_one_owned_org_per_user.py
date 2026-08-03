"""One person owns at most one organization.

Signing up with Google fired two POST /users?intent=signup at once: the auth
listener sends one when Firebase reports the account, and the sign-up screen
sent another straight after the popup closed. Each asks "do you already own an
organization", both asked before either had inserted its membership row, both
saw no, and both created one. One click, one email, two companies, and the
subscription attaches to whichever the person happened to be standing in.

The double call is removed, but that alone would not be a guarantee. A
double-click, a retry, or two tabs reproduce it, and the check-then-insert has
no way to be atomic across two requests. A partial unique index is where the
rule can actually be enforced: the second insert fails, and the caller resolves
to the organization that already exists.

Owning one while belonging to several is untouched. The index constrains rows
where role = 'owner' and says nothing about the rest, which is exactly the
product rule: you own one company and can be staff in any number.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260803_one_owner"
down_revision = "20260801_docs"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_one_owned_organization_per_user"


def upgrade():
    conn = op.get_bind()

    # Refuse to run rather than pick a winner. Deciding which of somebody's two
    # organizations survives is a judgement about their data — which holds the
    # documents, which the subscription is attached to — and a migration is the
    # wrong place to make it silently.
    duplicates = conn.execute(
        sa.text(
            """
            SELECT user_id, array_agg(organization_id ORDER BY organization_id) AS orgs
            FROM organization_members
            WHERE role = 'owner'
            GROUP BY user_id
            HAVING count(*) > 1
            """
        )
    ).fetchall()

    if duplicates:
        detail = "; ".join(f"user {row[0]} owns organizations {list(row[1])}" for row in duplicates)
        raise RuntimeError(
            "Cannot enforce one owned organization per user until existing duplicates are "
            f"resolved: {detail}. Decide which organization survives (the one holding the "
            "subscription and the documents), move or delete the other, then re-run."
        )

    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX {INDEX_NAME} ON organization_members (user_id) "
            "WHERE role = 'owner'"
        )
    )


def downgrade():
    op.execute(sa.text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
