"""Record which plan a subscription is on.

Seat pricing differs per plan — Starter includes 10 then charges $9, Business
includes 30 then charges $7 — so "how much would one more member cost?" cannot
be answered without knowing the plan. Stripe knows, via the price on the
subscription item, but asking Stripe on every page load to render a seat count
is a network round trip for a number that changes only at checkout.

Revision ID: 20260731_plan_key
Revises: 20260731_chat_ws
"""
from alembic import op
import sqlalchemy as sa


revision = '20260731_plan_key'
down_revision = '20260731_chat_ws'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subscriptions', sa.Column('plan_key', sa.String(), nullable=True))
    # Existing rows predate plans entirely: there was one STRIPE_PRICE_ID and
    # no tiers. Starter is the closest equivalent and the safer default, since
    # it grants fewer included seats than Business rather than more.
    op.execute("UPDATE subscriptions SET plan_key = 'starter' WHERE plan_key IS NULL")


def downgrade():
    op.drop_column('subscriptions', 'plan_key')
