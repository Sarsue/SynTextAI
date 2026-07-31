"""Plan definitions: the one place pricing is described.

The app's seat accounting reads these, and the Stripe prices were generated to
match: graduated tiers where the first tier is a flat amount covering the
included seats and the second charges per seat beyond them.

Changing a number here is not enough on its own. A Stripe price is immutable
once created, so a price change means creating a new price in Stripe with the
matching tiers and pointing STRIPE_PRICE_ID_<PLAN> at it. Edit one without the
other and the app quotes a figure the customer is not charged.
"""
import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    # Monthly base, in cents, covering everyone up to included_seats.
    base_cents: int
    included_seats: int
    # Charged per member beyond included_seats, in cents.
    overage_cents: int
    description: str

    def seats_price_cents(self, members: int) -> int:
        """What Stripe will bill for `members` seats, in cents.

        Mirrors the graduated tiers configured on the Stripe price. Used to
        show a customer the cost of adding someone *before* they commit to it,
        which is the difference between expanding seats and being surprised by
        an invoice.
        """
        overage = max(0, members - self.included_seats)
        return self.base_cents + overage * self.overage_cents

    @property
    def env_var(self) -> str:
        return f"STRIPE_PRICE_ID_{self.key.upper()}"

    def price_id(self) -> Optional[str]:
        return os.getenv(self.env_var)


STARTER = Plan(
    key="starter",
    name="Starter",
    base_cents=9900,
    included_seats=10,
    overage_cents=900,
    description="For a single practice getting its documents answering questions.",
)

BUSINESS = Plan(
    key="business",
    name="Business",
    base_cents=24900,
    included_seats=30,
    overage_cents=700,
    description="For multi-location groups, with a lower rate per added seat.",
)

PLANS: Dict[str, Plan] = {p.key: p for p in (STARTER, BUSINESS)}

DEFAULT_PLAN_KEY = "starter"


def get_plan(key: Optional[str]) -> Plan:
    """Resolve a plan key, falling back to the default rather than raising.

    A subscription row with a missing or unrecognised plan still has to render
    a billing page; defaulting is better than a 500 on the screen someone uses
    to fix their billing.
    """
    return PLANS.get((key or DEFAULT_PLAN_KEY).lower(), STARTER)


def plan_for_price_id(price_id: Optional[str]) -> Optional[Plan]:
    """Reverse-lookup used by the webhook, which only knows Stripe's price id."""
    if not price_id:
        return None
    for plan in PLANS.values():
        if plan.price_id() == price_id:
            return plan
    return None
