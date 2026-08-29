"""A credential a program can hold, when the only one we had needs a browser.

Every caller today arrives with a Firebase ID token, which exists because a
human signed in and dies about an hour later. Nothing without a person at a
keyboard can hold one, so nothing but our own site can call the API.

This is the second kind of credential: issued to an integration, alive until
revoked. What it deliberately does NOT carry is permission. There is no role
column and no workspace list frozen at creation. It names its creator and one
workspace, and every request looks up what that person may do right now. A key
made by an admin who is removed from the workspace tomorrow stops working
tomorrow, with nothing to revoke and nobody to remember.

`scopes` holds the same vocabulary OAuth will grant later, so the two are one
list of names rather than two that have to be reconciled.

Revision ID: 20260829_workspace_api_keys
Revises: 20260828_organization_events
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_workspace_api_keys"
down_revision: Union[str, None] = "20260828_organization_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        # The one workspace this credential may ever reach. A ceiling, not a
        # grant: it narrows what its creator can see, it never widens it.
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE, unlike organization_events.actor_user_id. An event is a
        # record of the past and stays true once the person is gone; this is
        # live authority borrowed from an account, so it cannot outlive it.
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # What a person calls it in Settings: "Claude desktop", "reporting
        # script". The only way to tell two rows apart when deciding which to
        # revoke, since the secret itself is never shown again.
        sa.Column("name", sa.String(), nullable=False),
        # The non-secret half of the token, stored in the clear so a lookup is
        # one indexed row rather than a scan and a hash comparison per row.
        # Also what Settings displays beside the revoke button.
        sa.Column("prefix", sa.String(), nullable=False),
        # SHA-256 of the full token. A fast hash on purpose: this is 256 bits
        # of randomness, not a password, so there is no dictionary to slow down
        # and this runs on every single request.
        sa.Column("token_hash", sa.String(), nullable=False),
        # The vocabulary an OAuth grant will use. A list because a credential
        # holding two scopes is normal, and a single column could not say it.
        sa.Column(
            "scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"knowledge:read\"]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Written on use, read in Settings. The column that makes an owner
        # willing to revoke something: nobody deletes a credential when they
        # cannot tell whether anything still depends on it.
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        # Withdrawn on purpose, as distinct from expires_at running out. Both
        # refuse; they read differently to somebody asking why an integration
        # stopped.
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        # NULL means no expiry, which is the default. Forced rotation nobody
        # asked for mostly produces integrations that break silently.
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    # Authentication looks up exactly one row by prefix, on every request.
    # Unique because the prefix is what identifies the row, and a duplicate
    # would make that lookup ambiguous at the moment it must not be.
    op.create_index(
        "ix_workspace_api_keys_prefix",
        "workspace_api_keys",
        ["prefix"],
        unique=True,
    )
    # Settings lists one workspace's keys.
    op.create_index(
        "ix_workspace_api_keys_workspace_id",
        "workspace_api_keys",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_api_keys_workspace_id", table_name="workspace_api_keys"
    )
    op.drop_index("ix_workspace_api_keys_prefix", table_name="workspace_api_keys")
    op.drop_table("workspace_api_keys")
