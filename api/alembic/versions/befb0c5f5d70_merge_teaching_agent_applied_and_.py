"""merge teaching_agent (applied) and workspace_members_invites (not yet applied)

Revision ID: befb0c5f5d70
Revises: 20260227_teaching_agent, add_workspace_members_invites
Create Date: 2026-07-24 10:40:46.489199

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'befb0c5f5d70'
down_revision: Union[str, None] = ('20260227_teaching_agent', 'add_workspace_members_invites')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
