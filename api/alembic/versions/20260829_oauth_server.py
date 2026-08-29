"""An authorization server, so connecting is a click rather than a pasted key.

An API key works and is the wrong shape for the customer this is sold to. It
asks somebody at a dental practice to generate a secret, keep it secret, and
paste it into a configuration file. OAuth asks them to press Allow.

Three tables, and the thing worth noticing is what none of them store:

  - `oauth_clients` is written by the client itself through dynamic
    registration, which is how MCP clients arrive: nobody pre-registers Claude
    with every server it might connect to. A client is not a credential, so
    registering one grants nothing at all until a person approves it.
  - `oauth_authorization_codes` exist for seconds and are consumed once. The
    PKCE challenge is stored, the verifier never is: that is the whole point of
    it, and a code intercepted in a redirect is useless without the verifier
    that never left the client.
  - `oauth_tokens` carry the same shape as an API key on purpose. A user, a
    workspace, scopes, and nothing about permission, so the live lookup in
    core/auth decides what the holder may do exactly as it does for a key.

Revision ID: 20260829_oauth_server
Revises: 20260829_workspace_api_keys
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_oauth_server"
down_revision: Union[str, None] = "20260829_workspace_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        # NULL for a public client, which is what a desktop app is: it cannot
        # keep a secret on somebody's laptop, so PKCE is what proves the token
        # request came from whoever started the authorization.
        sa.Column("client_secret_hash", sa.String(), nullable=True),
        # Shown on the consent screen. This is a name the client chose for
        # itself and nothing has verified it, which is why the screen names the
        # redirect host too.
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("redirect_uris", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"], unique=True)

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        # Stored so /token can check the verifier against it. The verifier is
        # never stored, anywhere, at any point.
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("code_challenge_method", sa.String(16), nullable=False),
        # Kept because the token request must present the same one. A code
        # redeemed against a different redirect_uri is a redirect that was
        # tampered with.
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        # Set on first use. A code presented twice is not merely refused: it
        # means somebody else may have had it, and the tokens it produced are
        # withdrawn.
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "ix_oauth_codes_hash", "oauth_authorization_codes", ["code_hash"], unique=True
    )

    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        # The same CASCADE as an API key, and for the same reason: this is live
        # authority borrowed from an account and must not outlive it.
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("access_prefix", sa.String(), nullable=False),
        sa.Column("access_hash", sa.String(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(), nullable=False),
        # A refresh token has no expiry of its own. It dies when the grant is
        # revoked, which is the row somebody actually looks at in Settings.
        sa.Column("refresh_prefix", sa.String(), nullable=True),
        sa.Column("refresh_hash", sa.String(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    # Authentication reads exactly one row by access_prefix on every request.
    op.create_index("ix_oauth_tokens_access_prefix", "oauth_tokens", ["access_prefix"], unique=True)
    op.create_index("ix_oauth_tokens_refresh_prefix", "oauth_tokens", ["refresh_prefix"])
    # Settings lists one workspace's connections, keys and grants together.
    op.create_index("ix_oauth_tokens_workspace_id", "oauth_tokens", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_tokens_workspace_id", table_name="oauth_tokens")
    op.drop_index("ix_oauth_tokens_refresh_prefix", table_name="oauth_tokens")
    op.drop_index("ix_oauth_tokens_access_prefix", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_index("ix_oauth_codes_hash", table_name="oauth_authorization_codes")
    op.drop_table("oauth_authorization_codes")
    op.drop_index("ix_oauth_clients_client_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
