"""An invite says what the person will be, not just that they may join.

Every invite produced a staff member who could see every workspace, because
that is what the accept path hardcoded. The owner then had to find them in the
members list afterwards and set the role and reach they had meant all along, on
a different screen, after the person had already been let in with more access
than intended.

The decision belongs at the moment of inviting, so the invite has to carry it.

role   'staff' or 'admin'
scope  'organization' for every workspace including later ones, or 'workspace'
       for the ones named in workspace_ids

workspace_ids is a list because access is a set: somebody can belong to three of
five workspaces, which the single nullable workspace_id could not express. That
column stays for the workspace-scoped invites already in the table, and for the
membership row the accept path still writes.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260803_invite_rs"
down_revision = "20260803_one_owner"
branch_labels = None
depends_on = None


def upgrade():
    # Defaults match what every existing pending invite would have produced when
    # accepted, so nothing in flight changes meaning.
    op.add_column(
        "workspace_invites",
        sa.Column("role", sa.String(), nullable=False, server_default="staff"),
    )
    op.add_column(
        "workspace_invites",
        sa.Column("scope", sa.String(), nullable=False, server_default="organization"),
    )
    op.add_column(
        "workspace_invites",
        sa.Column("workspace_ids", sa.JSON(), nullable=True),
    )

    # A workspace-scoped invite that named one workspace becomes the same thing
    # expressed as a set, so the accept path has one shape to read.
    op.execute(
        sa.text(
            """
            UPDATE workspace_invites
            SET scope = 'workspace',
                workspace_ids = json_build_array(workspace_id)::json
            WHERE workspace_id IS NOT NULL
            """
        )
    )


def downgrade():
    op.drop_column("workspace_invites", "workspace_ids")
    op.drop_column("workspace_invites", "scope")
    op.drop_column("workspace_invites", "role")
