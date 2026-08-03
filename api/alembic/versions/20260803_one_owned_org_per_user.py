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

    for user_id, orgs in duplicates:
        orgs = list(orgs)

        # The keeper is decided by evidence, in the order that matters to the
        # customer: the company being paid for, then the one holding documents,
        # then the oldest. Every duplicate this migration exists for came from a
        # double signup seconds apart, so usually only the first rule fires.
        keeper = conn.execute(
            sa.text(
                """
                SELECT o.id
                FROM organizations o
                LEFT JOIN subscriptions s
                       ON s.organization_id = o.id
                      AND lower(s.status) IN ('active', 'trialing')
                LEFT JOIN workspaces w ON w.organization_id = o.id
                LEFT JOIN files f ON f.workspace_id = w.id
                WHERE o.id = ANY(:orgs)
                GROUP BY o.id, s.id
                ORDER BY (s.id IS NOT NULL) DESC, count(f.id) DESC, o.id ASC
                LIMIT 1
                """
            ),
            {"orgs": orgs},
        ).scalar()

        losers = [o for o in orgs if o != keeper]

        # Never delete an organization holding documents. If one does, this is
        # not the duplicate-signup case and a migration must not guess: two
        # companies with real content is a merge, and a merge is somebody's
        # decision, not a schema change.
        with_files = conn.execute(
            sa.text(
                """
                SELECT o.id, count(f.id) AS n
                FROM organizations o
                LEFT JOIN workspaces w ON w.organization_id = o.id
                LEFT JOIN files f ON f.workspace_id = w.id
                WHERE o.id = ANY(:orgs)
                GROUP BY o.id
                HAVING count(f.id) > 0
                """
            ),
            {"orgs": losers},
        ).fetchall()

        if with_files:
            detail = ", ".join(f"organization {row[0]} holds {row[1]} document(s)" for row in with_files)
            raise RuntimeError(
                f"User {user_id} owns organizations {orgs} and more than one holds documents "
                f"({detail}). Resolve by hand: decide which survives, move the documents, then "
                "re-run. This migration will not merge companies."
            )

        for org in losers:
            conn.execute(sa.text("DELETE FROM organizations WHERE id = :id"), {"id": org})
            print(f"  removed empty duplicate organization {org} owned by user {user_id}; kept {keeper}")

    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX {INDEX_NAME} ON organization_members (user_id) "
            "WHERE role = 'owner'"
        )
    )


def downgrade():
    op.execute(sa.text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
