"""Create the Stripe products and graduated prices described in core/plans.py.

Graduated tiers rather than a base item plus a per-seat item: one subscription
item whose quantity is the member count, with Stripe doing the arithmetic. That
leaves a single number to keep in sync instead of two, and produces an invoice
the customer can actually read.

    Starter   tier 1  up_to 10   flat $99   unit $0
              tier 2  up_to inf             unit $9

Run against test mode first. STRIPE_SECRET decides which mode you hit — a
sk_test_ key writes to test data, sk_live_ writes to the real account:

    python -m api.scripts.create_stripe_prices --dry-run
    python -m api.scripts.create_stripe_prices

Prints the env lines to paste once created. Safe to re-run: a product with the
same plan key in metadata is reused rather than duplicated, but note that a
*price* is immutable in Stripe, so changing an amount always means a new price.
"""

import argparse
import logging
import os
import sys

import stripe
from dotenv import load_dotenv

from api.core.plans import PLANS, Plan

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("create_stripe_prices")


def find_product(plan: Plan):
    """Reuse a product previously created for this plan, if there is one."""
    for product in stripe.Product.list(limit=100, active=True).auto_paging_iter():
        # to_dict() rather than .get or dict(): StripeObject routes attribute
        # access through __getattr__ so .get raises AttributeError, and dict()
        # iterates it positionally and raises KeyError: 0.
        metadata = product.metadata.to_dict() if product.metadata else {}
        if metadata.get("plan_key") == plan.key:
            return product
    return None


def build_tiers(plan: Plan):
    return [
        {
            "up_to": plan.included_seats,
            "flat_amount": plan.base_cents,
            "unit_amount": 0,
        },
        {
            "up_to": "inf",
            "unit_amount": plan.overage_cents,
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would be created")
    args = parser.parse_args()

    secret = os.getenv("STRIPE_SECRET")
    if not secret:
        logger.error("STRIPE_SECRET is not set")
        return 1
    stripe.api_key = secret

    mode = "LIVE" if secret.startswith("sk_live_") else "test"
    logger.info("Stripe mode: %s", mode)
    if mode == "LIVE" and not args.dry_run:
        # Live prices are customer-facing and cannot be deleted, only
        # deactivated. Refuse to do that as a side effect of running a script.
        logger.error(
            "Refusing to create LIVE prices without an explicit confirmation. "
            "Re-run with STRIPE_ALLOW_LIVE=1 if that is genuinely intended."
        )
        if os.getenv("STRIPE_ALLOW_LIVE") != "1":
            return 1

    env_lines = []
    for plan in PLANS.values():
        tiers = build_tiers(plan)
        summary = (
            f"{plan.name}: ${plan.base_cents / 100:.0f} for {plan.included_seats} seats, "
            f"then ${plan.overage_cents / 100:.0f}/seat"
        )

        if args.dry_run:
            logger.info("WOULD CREATE %s", summary)
            logger.info("  tiers=%s", tiers)
            continue

        product = find_product(plan)
        if product:
            logger.info("reusing product %s for %s", product.id, plan.name)
        else:
            product = stripe.Product.create(
                name=f"SyntextAI {plan.name}",
                description=plan.description,
                metadata={"plan_key": plan.key},
            )
            logger.info("created product %s for %s", product.id, plan.name)

        price = stripe.Price.create(
            product=product.id,
            currency="usd",
            recurring={"interval": "month"},
            billing_scheme="tiered",
            tiers_mode="graduated",
            tiers=tiers,
            metadata={
                "plan_key": plan.key,
                "included_seats": str(plan.included_seats),
            },
        )
        logger.info("created price %s — %s", price.id, summary)
        env_lines.append(f"{plan.env_var}={price.id}")

    if env_lines:
        print("\nAdd these to your env file:\n")
        for line in env_lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
