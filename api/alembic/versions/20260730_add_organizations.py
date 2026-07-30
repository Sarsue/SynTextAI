"""Introduce organizations as the tenant

Revision ID: 20260730_orgs
Revises: b8f6b3f63f7d
Create Date: 2026-07-30

Until now there was no tenant entity. Workspace was doing three unrelated jobs
at once: billing entity (workspaces.user_id implied who pays), security boundary
(workspace membership granted access), and document container. Subscription
attached to a *user* rather than a company, so an invited colleague read as a
separate customer and got pushed into their own trial. Whether someone was a
mere invitee had to be guessed from "owns zero workspaces", which is a proxy,
not a fact.

This adds the missing entity:

    organizations         the tenant. Billing entity and security boundary.
    organization_members  user <-> org with a role. Membership is now recorded,
                          not inferred.
    workspaces.organization_id    a workspace belongs to an org, many per org
    subscriptions.organization_id the org pays, not a person
    subscriptions.seats           seat allowance, NULL meaning unlimited

Backfill gives every existing user their own organization and makes them its
owner, moves their workspaces and subscription onto it, and converts existing
workspace_members rows into organization_members of the workspace's org. Doing
this while there is a single customer is deliberate: the data at risk is billing
data, and every additional customer makes it heavier.

workspaces.user_id and workspace_members are intentionally left in place. They
become "who created it" and legacy respectively, so this migration can be rolled
back without data loss and so no read path breaks mid-deploy.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260730_orgs"
down_revision = "b8f6b3f63f7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # owner: billing plus everything. admin: manage members, no billing.
        # member: use it.
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),
    )
    op.create_index("ix_organization_members_org_id", "organization_members", ["organization_id"])
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])

    op.add_column("workspaces", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_organization_id",
        "workspaces",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])

    op.add_column("subscriptions", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_subscriptions_organization_id",
        "subscriptions",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_subscriptions_organization_id", "subscriptions", ["organization_id"])
    # NULL means unlimited, which is how the Business plan is sold.
    op.add_column("subscriptions", sa.Column("seats", sa.Integer(), nullable=True))

    # ---------------- backfill ----------------

    # One organization per existing user, named from the email local part so it
    # is recognisable in the admin UI without inventing data.
    op.execute(
        """
        INSERT INTO organizations (name, created_at, updated_at)
        SELECT COALESCE(NULLIF(split_part(u.email, '@', 1), ''), 'Organization') || '''s Organization',
               now(), now()
        FROM users u
        ORDER BY u.id
        """
    )

    # Pair each user with the org just created for them. Both sets are ordered
    # by id, so row_number lines them up deterministically.
    op.execute(
        """
        CREATE TEMPORARY TABLE _user_org AS
        WITH u AS (
            SELECT id AS user_id, row_number() OVER (ORDER BY id) AS rn FROM users
        ), o AS (
            SELECT id AS organization_id, row_number() OVER (ORDER BY id) AS rn FROM organizations
        )
        SELECT u.user_id, o.organization_id FROM u JOIN o ON o.rn = u.rn
        """
    )

    op.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role, joined_at)
        SELECT organization_id, user_id, 'owner', now() FROM _user_org
        """
    )

    op.execute(
        """
        UPDATE workspaces w
        SET organization_id = uo.organization_id
        FROM _user_org uo
        WHERE uo.user_id = w.user_id
        """
    )

    op.execute(
        """
        UPDATE subscriptions s
        SET organization_id = uo.organization_id
        FROM _user_org uo
        WHERE uo.user_id = s.user_id
        """
    )

    # Existing workspace staff become members of that workspace's organization.
    # ON CONFLICT protects the case where someone is already the owner.
    op.execute(
        """
        INSERT INTO organization_members (organization_id, user_id, role, joined_at)
        SELECT DISTINCT w.organization_id, wm.user_id, 'member', COALESCE(wm.joined_at, now())
        FROM workspace_members wm
        JOIN workspaces w ON w.id = wm.workspace_id
        WHERE w.organization_id IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_organization_member DO NOTHING
        """
    )

    op.execute("DROP TABLE IF EXISTS _user_org")

    # Every workspace must now belong to an org. Enforce it so nothing can be
    # created outside a tenant.
    op.alter_column("workspaces", "organization_id", nullable=False)


def downgrade() -> None:
    op.alter_column("workspaces", "organization_id", nullable=True)

    op.drop_column("subscriptions", "seats")
    op.drop_index("ix_subscriptions_organization_id", table_name="subscriptions")
    op.drop_constraint("fk_subscriptions_organization_id", "subscriptions", type_="foreignkey")
    op.drop_column("subscriptions", "organization_id")

    op.drop_index("ix_workspaces_organization_id", table_name="workspaces")
    op.drop_constraint("fk_workspaces_organization_id", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "organization_id")

    op.drop_index("ix_organization_members_user_id", table_name="organization_members")
    op.drop_index("ix_organization_members_org_id", table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_table("organizations")
