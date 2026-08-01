"""Seat accounting: keeping the Stripe subscription quantity equal to headcount.

The subscription item's quantity is the number of people in the organization.
Stripe's graduated tiers turn that into a bill — the plan's included seats are
covered by the base amount, and anything beyond is charged per seat. So the
only thing this module has to get right is: quantity == members.

Adding a member never blocks. That was a deliberate choice: refusing an invite
at the seat boundary puts friction in front of a customer at the exact moment
they are expanding. Instead the quantity grows and the customer is charged,
which is what "every added member costs something" means in practice.
"""
import logging
from typing import Any, Dict, Optional

import stripe

from ..repositories.repository_manager import RepositoryManager
from .plans import get_plan

logger = logging.getLogger(__name__)


async def seat_summary(store: RepositoryManager, organization_id: int) -> Dict[str, Any]:
    """What the organization is using, and what one more person would cost.

    The cost is computed so the UI can state it before anyone commits to an
    invite. A seat charge that appears only on the next invoice is a support
    ticket.
    """
    members = await store.org_repo.count_members(organization_id)

    subscription = await store.org_repo.get_subscription_row(organization_id)
    plan = get_plan((subscription or {}).get("plan_key"))

    return {
        "members": members,
        "included_seats": plan.included_seats,
        "plan": plan.key,
        "plan_name": plan.name,
        # Zero while there are still included seats left, which is what makes
        # "adding someone is free until 10" truthful in the UI.
        "next_seat_cents": plan.seats_price_cents(members + 1) - plan.seats_price_cents(members),
        "monthly_cents": plan.seats_price_cents(members),
    }


async def sync_seats_to_stripe(
    store: RepositoryManager,
    organization_id: int,
    *,
    reason: str = "membership change",
) -> Optional[int]:
    """Set the Stripe subscription quantity to the organization's headcount.

    Returns the quantity written, or None when there is nothing to sync.

    Deliberately never raises. A membership change that succeeded in the
    database must not be rolled back because Stripe was briefly unreachable —
    the customer would see an invite fail for reasons that have nothing to do
    with them. Drift is recoverable: the webhook and the next membership change
    both re-assert the quantity. Losing the member is not.
    """
    subscription = await store.org_repo.get_subscription_row(organization_id)
    if not subscription or not subscription.get("stripe_subscription_id"):
        logger.info("Org %s has no Stripe subscription; nothing to sync", organization_id)
        return None

    members = await store.org_repo.count_members(organization_id)

    try:
        stripe_sub = await stripe.Subscription.retrieve_async(subscription["stripe_subscription_id"])
        items = stripe_sub["items"]["data"]
        if not items:
            logger.error("Stripe subscription %s has no items", stripe_sub.id)
            return None

        item = items[0]
        # item["quantity"], not item.get("quantity"). A StripeObject raises
        # AttributeError on .get, and this whole function is wrapped in a
        # never-raise handler, so the sync failed at its first line every single
        # time and logged it as a Stripe problem. Nothing was ever billed: an
        # organization with two members sat at quantity 1 while every call site
        # believed it had synced. A dict-like object that is not a dict, inside
        # a function that swallows errors on purpose, is how a revenue path
        # stays broken without anyone seeing it.
        if item["quantity"] == members:
            return members

        await stripe.SubscriptionItem.modify_async(
            item["id"],
            quantity=members,
            # Charge the difference now rather than at renewal, so a seat added
            # on day 2 is not effectively free for 28 days.
            proration_behavior="always_invoice",
        )
        logger.info(
            "Synced org %s to %s seats (%s), subscription %s",
            organization_id, members, reason, stripe_sub.id,
        )
        return members
    except Exception as e:
        logger.error(
            "Could not sync seats for org %s (%s): %s",
            organization_id, reason, e, exc_info=True,
        )
        return None
