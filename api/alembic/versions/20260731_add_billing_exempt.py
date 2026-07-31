"""Mark organizations that use the product without paying.

This is how demo and evaluation access works now that there is no trial: an
organization is created for the prospect and flagged, and the flag is cleared
when the demo ends. Their data stays; their access stops.

A column rather than an env var of organization ids, because exemption is a
property of a particular organization and not of the deployment. Starting or
ending a demo should not require a redeploy, and prod and local should not
disagree about who is exempt because their id sequences differ.

Revision ID: 20260731_exempt
Revises: 20260731_plan_key
"""
from alembic import op
import sqlalchemy as sa


revision = '20260731_exempt'
down_revision = '20260731_plan_key'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'organizations',
        sa.Column(
            'billing_exempt',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column('organizations', 'billing_exempt')
