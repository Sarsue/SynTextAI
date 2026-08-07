from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Body, status
from fastapi.responses import JSONResponse
from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel
import stripe
import logging
import os
from dotenv import load_dotenv
from ..core.utils import get_user_id
from ..core.limits import entitlement_for_organization, resolve_entitlement
from ..core.plans import PLANS, get_plan, plan_for_price_id
from ..core.seats import seat_summary, sync_seats_to_stripe
from ..core.permissions import Capability, assert_organization_capability
from ..core.auth import authenticate_user, get_store
from api.repositories.repository_manager import RepositoryManager
import asyncio
# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
subscriptions_router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# Stripe configuration. Price ids live on the plans, not here: there is no
# longer a single price, and each plan's graduated tiers encode its own
# included seats and overage rate.
stripe.api_key = os.getenv('STRIPE_SECRET')
endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')

def _read(obj, key, default=None):
    """Read a field off a Stripe object, or a plain dict, without raising.

    A StripeObject is not a dict and routes attribute lookups through
    __getattr__, so `.get` raises AttributeError rather than returning a
    default. Every place that forgot this failed silently or fatally: the seat
    sync swallowed it and never billed anybody, and the webhook raised on the
    very first field it touched. Subscript access works on both shapes, so this
    is the only way these objects should be read.
    """
    try:
        return obj[key]
    except Exception:
        return default


def _client_secret(stripe_subscription) -> Optional[str]:
    """The secret the browser needs to finish a 3D Secure challenge, if one is owed.

    A card that requires authentication does not decline. Stripe accepts the
    subscription, leaves it `incomplete`, and waits for the cardholder to pass a
    challenge in their bank's popup. Nothing else happens until the browser
    confirms, so a server that only reads `.status` sees "incomplete" and has no
    way to tell the customer what to do about it.

    WHERE THIS LIVES, AND WHY IT IS NOT WHERE THE INTERNET SAYS

    Every guide reaches for `latest_invoice.payment_intent`. On this account's
    API version (2026-07-29.dahlia) that field is **null** — it was removed in
    the Basil-era rewrite of invoices. Expanding it costs nothing, raises
    nothing, and yields nothing, which is the worst possible failure: the code
    looks right and silently never finds a secret. Verified against Stripe test
    mode, not assumed; `latest_invoice.confirmation_secret` is where it is now.

    Returns None when no challenge is owed, which is the normal path.
    """
    invoice = _read(stripe_subscription, "latest_invoice")
    secret = _read(invoice, "confirmation_secret") if invoice is not None else None
    return _read(secret, "client_secret") if secret is not None else None


def _period_end(stripe_subscription) -> Optional[datetime]:
    """When the current billing period ends, as a datetime.

    Stripe moved current_period_end off the subscription and onto the
    subscription item in API version 2026-06-24. Reading it from the top level
    raised AttributeError *after* the subscription had already been created and
    charged, so the customer paid and the app answered 400: Stripe said active,
    the database said none, and retrying created a second subscription.

    Reads the item first, falls back to the subscription for older versions,
    and returns None rather than raising — a missing renewal date is a display
    detail, not a reason to fail a subscription that Stripe has accepted.
    """
    items = _read(stripe_subscription, "items")
    data = _read(items, "data") if items is not None else None
    for item in data or []:
        value = _read(item, "current_period_end")
        if value:
            return datetime.utcfromtimestamp(value)

    value = _read(stripe_subscription, "current_period_end")
    return datetime.utcfromtimestamp(value) if value else None


async def _billing_organization_id(
    store: RepositoryManager, user_id: int, organization_id: Optional[int] = None
) -> Optional[int]:
    """The organization a subscription belongs to.

    Pass organization_id: it is the tenant the customer is actually in.

    Without it this walked the membership list and took the first organization
    where the user was owner *or admin*, which is not the same question. An
    admin of somebody else's company who signed up for their own was billed
    against the company that had invited them — creating a second live
    subscription on an organization that already paid, while their own stayed
    unpaid and locked. Real money, on the wrong account.

    Falls back to an organization they *own*, never merely administer, since
    billing is an owner's to hold.
    """
    memberships = await store.org_repo.get_memberships(user_id)

    if organization_id is not None:
        match = next(
            (m for m in memberships if m["organization_id"] == organization_id), None
        )
        return match["organization_id"] if match else None

    for m in memberships:
        if m["role"] == "owner":
            return m["organization_id"]
    return None


# Route to get subscription status
@subscriptions_router.get("/status")
async def subscription_status(
    organization_id: Optional[int] = Query(None, description="The organization being viewed"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Billing state for the organization the caller is in.

    Resolved by user before, which is a different question. Somebody who owns
    an unpaid company while belonging to a paid one was shown the paid
    subscription on their own company's billing page: the banner said "no plan
    yet" and the panel underneath said "your subscription is active", with the
    plan picker hidden because the page believed there was nothing to buy. Two
    sources of truth on one screen, disagreeing.
    """
    try:
        user_id = user_data["user_id"]
        subscription_data = None
        if organization_id is not None:
            # Belong to it before asking about its billing.
            #
            # organization_id came straight off the query string and was used
            # unchecked, so any signed-in person could read any company's
            # subscription status and renewal date by guessing a small integer.
            # Not the card, which is filtered further down, but enough to learn
            # that a named competitor pays and when they renew.
            if await store.org_repo.get_role(organization_id, user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of this organization.",
                )
            row = await store.org_repo.get_subscription_row(organization_id)
            if row and row.get("id"):
                # Reuse the per-user reader for card details, but only when the
                # subscription it returns is the one this organization holds.
                by_user = await store.user_repo.get_subscription(user_id)
                if by_user and by_user[0].get("id") == row["id"]:
                    subscription_data = by_user
                else:
                    subscription_data = (row, None)
        else:
            subscription_data = await store.user_repo.get_subscription(user_id)

        # Answer for the organization when one is named, and only fall back to
        # the person when none is.
        #
        # resolve_entitlement asks "does any company of yours pay", which is the
        # right question for an unscoped call and the wrong one here: somebody
        # owning a paying company and an unpaid one was told the unpaid one was
        # entitled, so its billing page said "no plan yet" above and "your
        # subscription is active" below. A staff member invited into a paid
        # company is still covered, because entitlement now comes from that
        # company's own subscription rather than from anything they hold.
        if organization_id is not None:
            entitlement = await entitlement_for_organization(store, user_id, organization_id)
        else:
            entitlement = await resolve_entitlement(store, user_id)

        if not subscription_data:
            return {
                'subscription_status': 'none',
                'card_last4': None,
                'card_brand': None,
                'card_exp_month': None,
                'card_exp_year': None,
                'trial_end': None,
                'current_period_end': None,
                'has_active_payment_method': False,
                'entitled': entitlement['entitled'],
                'entitlement_source': entitlement['source'],
                'is_org_owner': entitlement['is_org_owner'],
                'is_member_only': entitlement['is_member_only'],
            }

        # Unpack the tuple (subscription_dict, card_details_dict)
        subscription, card_details = subscription_data

        card_last4 = (card_details or {}).get('card_last4')
        card_brand = (card_details or {}).get('card_type')
        card_exp_month = (card_details or {}).get('exp_month')
        card_exp_year = (card_details or {}).get('exp_year')

        trial_end = subscription.get('trial_end')
        current_period_end = subscription.get('current_period_end')

        response = {
            'subscription_status': subscription.get('status'),
            'card_last4': card_last4,
            'card_brand': card_brand,
            'card_exp_month': card_exp_month,
            'card_exp_year': card_exp_year,
            'trial_end': trial_end.isoformat() if isinstance(trial_end, datetime) else trial_end,
            'current_period_end': current_period_end.isoformat() if isinstance(current_period_end, datetime) else current_period_end,
            'has_active_payment_method': bool(card_last4 and card_brand),
            'entitled': entitlement['entitled'],
            'entitlement_source': entitlement['source'],
            'is_org_owner': entitlement['is_org_owner'],
            'is_member_only': entitlement['is_member_only'],
        }

        return response
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error in subscription_status: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal error occurred")

@subscriptions_router.get("/plans")
async def list_plans():
    """The plans a customer can buy, priced for the UI to render directly.

    Amounts come from core/plans.py, the same definition the Stripe prices were
    generated from, so the page cannot advertise a price the customer will not
    be charged.
    """
    return {
        "plans": [
            {
                "key": plan.key,
                "name": plan.name,
                "description": plan.description,
                "base_cents": plan.base_cents,
                "included_seats": plan.included_seats,
                "overage_cents": plan.overage_cents,
                "available": bool(plan.price_id()),
            }
            for plan in PLANS.values()
        ]
    }


@subscriptions_router.get("/seats")
async def get_seats(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Seats used, seats included, and what the next one costs.

    The next-seat price is here so the invite dialog can say what adding
    somebody will cost before it happens. A seat charge discovered on the next
    invoice is a support ticket.
    """
    organization_id = await _billing_organization_id(store, user_data["user_id"])
    if organization_id is None:
        raise HTTPException(
            status_code=403,
            detail="Your access is provided by your team's plan. Contact your workspace owner about billing.",
        )
    return await seat_summary(store, organization_id)


# Route to cancel a subscription
@subscriptions_router.post("/cancel", status_code=200)
async def cancel_sub(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription_data = await store.user_repo.get_subscription(user_id)
        if not subscription_data:
            raise HTTPException(status_code=404, detail="No subscription found")

        subscription, card_details = subscription_data
        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Subscription ID is missing")

        cancellation_result = await asyncio.to_thread(stripe.Subscription.delete, subscription_id)
    
        await store.user_repo.update_subscription_status(
            subscription.get('stripe_customer_id'),
            cancellation_result.status
        )
        return {
            'subscription_status': cancellation_result['status']
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling subscription: {e}", exc_info=True)
        raise HTTPException(status_code=403, detail="Could not cancel subscription")

# Route to create a subscription
@subscriptions_router.post("/subscribe", status_code=201)
async def create_subscription(
    payment_method: str = Body(..., embed=True),
    plan: str = Body("starter", embed=True),
    organization_id: Optional[int] = Body(None, embed=True),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Subscribe the caller's organization, with quantity set to its headcount.

    There is no trial. Access is bought with a card, or granted by being
    invited into an organization that has already bought it — which is how
    demos work: a prospect joins Osas Inc rather than starting a trial of
    their own.
    """
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]
        selected_plan = get_plan(plan)
        if selected_plan.key != (plan or "").lower():
            logger.warning("Unknown plan %r requested by user %s; using %s", plan, user_id, selected_plan.key)
        price_id = selected_plan.price_id()
        if not price_id:
            logger.error("No Stripe price configured for plan %s (%s)", selected_plan.key, selected_plan.env_var)
            raise HTTPException(status_code=503, detail="Billing is not configured for that plan yet.")

        billing_org_id = await _billing_organization_id(store, user_id, organization_id)
        if billing_org_id is None:
            raise HTTPException(
                status_code=403,
                detail="Your access is provided by your team's plan. Contact your workspace owner about billing.",
            )

        # Billing belongs to the owner. An admin runs the workspace; somebody
        # has to be unable to put a second subscription on it.
        await assert_organization_capability(
            store, user_id, billing_org_id, Capability.MANAGE_BILLING
        )

        # One live subscription per organization. Without this an admin, or an
        # owner who reached checkout twice, could stack a second one on a tenant
        # that already pays.
        existing_org_status = await store.org_repo.get_subscription_status(billing_org_id)
        if (existing_org_status or "none").lower() in {"active", "trialing"}:
            raise HTTPException(
                status_code=400,
                detail="This organization already has an active subscription.",
            )
        organization_id = billing_org_id
        # Quantity is headcount. Stripe's graduated tiers do the rest: the base
        # amount covers the included seats and only the excess is charged.
        member_count = max(1, await store.org_repo.count_members(organization_id))

        subscription_data = await store.user_repo.get_subscription(user_id)
        stripe_customer_id = None
        if subscription_data:
            subscription, _ = subscription_data
            stripe_customer_id = subscription.get('stripe_customer_id')
            if subscription.get('status') == 'active':
                logger.error(f"Request came from an already active subscription: {user_id}")
                raise HTTPException(status_code=400, detail="Active subscription already exists")

            # An abandoned 3D Secure challenge leaves an unpaid subscription
            # behind, and the only guard above is on 'active', so every retry
            # created another one. Stripe expires them after 23 hours, so this
            # never double-charged, but it left a customer's account holding a
            # row of dead subscriptions and made their billing history unreadable.
            #
            # Ask Stripe before cancelling anything. Our row says what we last
            # heard, and the whole reason this endpoint exists is that we
            # sometimes hear late: a customer who passed the challenge while our
            # webhook was still in flight has an ACTIVE subscription behind an
            # 'incomplete' row. Cancelling on the strength of that row would end
            # a paying customer's subscription and charge them again for a new
            # one. The row is a cache. Stripe is the fact.
            if subscription.get('status') == 'incomplete' and subscription.get('stripe_subscription_id'):
                stale_id = subscription['stripe_subscription_id']
                try:
                    live = await stripe.Subscription.retrieve_async(stale_id)
                except Exception as e:
                    live = None
                    logger.info("Could not read subscription %s: %s", stale_id, e)

                if live is not None and live.status in {'active', 'trialing'}:
                    # They already paid. Heal the row instead of selling again.
                    await store.user_repo.update_subscription(
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stale_id,
                        status=live.status,
                        current_period_end=_period_end(live),
                    )
                    logger.info("Subscription %s was already %s; refreshed the stale row", stale_id, live.status)
                    raise HTTPException(
                        status_code=400,
                        detail="This organization already has an active subscription.",
                    )

                if live is not None and live.status == 'incomplete':
                    try:
                        await stripe.Subscription.cancel_async(stale_id)
                        logger.info("Cleared abandoned incomplete subscription for user %s", user_id)
                    except Exception as e:
                        # Already gone or already expired is the common case,
                        # and neither is a reason to stop somebody paying.
                        logger.info("Could not clear incomplete subscription for user %s: %s", user_id, e)
   
        # Retrieve the payment method ID from the request
        payment_method_id = payment_method
        if not payment_method_id:
            logger.error(f"Request came without a valid payment method ID: {user_id}")
            raise HTTPException(status_code=400, detail="Payment method ID is missing")

        # The customer is the organization, not the person.
        #
        # This searched Stripe by email and took the first hit, so somebody who
        # owns two companies billed both to one customer. Two subscriptions then
        # shared a customer id, which is the only key the webhook had, and a
        # person who owns one company while belonging to another is the model
        # this product is built around. Matching on organization_id in metadata
        # keeps one customer per tenant, and the email is still recorded so the
        # Stripe dashboard reads the way you expect.
        if not stripe_customer_id:
            found = await stripe.Customer.search_async(
                query=f"metadata['organization_id']:'{organization_id}'"
            )
            if found.data:
                stripe_customer_id = found.data[0].id
                logger.info(f"Reusing Stripe customer {stripe_customer_id} for org {organization_id}")
            else:
                customer = await stripe.Customer.create_async(
                    description=f"Organization {organization_id}",
                    email=user_info.get('email'),
                    name=user_info.get('name'),
                    metadata={"organization_id": str(organization_id), "owner_user_id": str(user_id)},
                )
                stripe_customer_id = customer.id
                logger.info(f"Created Stripe customer {stripe_customer_id} for org {organization_id}")

        try:
            # Attach the payment method to the customer
            payment_method = await stripe.PaymentMethod.retrieve_async(payment_method_id)
            await stripe.PaymentMethod.attach_async(
                payment_method_id,
                customer=stripe_customer_id
            )

            # Set the payment method as the default for the customer
            await stripe.Customer.modify_async(
                stripe_customer_id,
                invoice_settings={'default_payment_method': payment_method_id}
            )

            # Create a new Stripe subscription.
            #
            # The expand is load-bearing: without it the confirmation secret is
            # not in the response at all, and a card that needs 3D Secure leaves
            # the customer stuck with no way to finish. See _client_secret.
            created_subscription = await stripe.Subscription.create_async(
                customer=stripe_customer_id,
                items=[{'price': price_id, 'quantity': member_count}],
                default_payment_method=payment_method_id,
                expand=['latest_invoice.confirmation_secret'],
            )
            # Only an unpaid subscription owes anybody a challenge. Stripe puts
            # a confirmation secret on the invoice either way, so the secret's
            # presence says nothing: a card that sailed through on the first
            # attempt comes back 'active' with a secret attached. Gating on the
            # secret alone sent every ordinary customer through a pointless
            # confirmCardPayment round trip against an intent that had already
            # succeeded. The status is the thing that distinguishes them.
            client_secret = _client_secret(created_subscription)
            requires_action = created_subscription.status == 'incomplete' and bool(client_secret)

            # Store the subscription in the database
            await store.user_repo.add_or_update_subscription(
                user_id=user_id,
                organization_id=organization_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=created_subscription.id,
                status=created_subscription.status,
                current_period_end=_period_end(created_subscription),
                card_last4=payment_method.card.last4,
                card_type=payment_method.card.brand,
                exp_month=payment_method.card.exp_month,
                exp_year=payment_method.card.exp_year,
                seats=selected_plan.included_seats,
                plan_key=selected_plan.key,
            )

            return {
                'message': 'Subscription created successfully',
                "subscription_status": created_subscription.status,
                # Decided here rather than in the browser, so there is one
                # definition of "this customer still has something to do".
                'requires_action': requires_action,
                'client_secret': client_secret if requires_action else None,
                'plan': selected_plan.key,
                'plan_name': selected_plan.name,
                'seats_included': selected_plan.included_seats,
                'seats_used': member_count,
                'card_last4': payment_method.card.last4,
                'card_brand': payment_method.card.brand,
                'card_exp_month': payment_method.card.exp_month,
                'card_exp_year': payment_method.card.exp_year,
                'trial_end': None,
                'current_period_end': (lambda d: d.isoformat() if d else None)(_period_end(created_subscription)),
                'has_active_payment_method': True,
            }

        except stripe.error.CardError as e:
            # Stripe's own card-decline messages are written to be shown to the
            # customer (e.g. "Your card has insufficient funds") — safe to surface,
            # unlike a generic exception which might carry internal detail.
            logger.info(f"Card declined for user {user_id}: {e.user_message or e}")
            await store.user_repo.add_or_update_subscription(
                user_id=user_id,
                organization_id=await _billing_organization_id(store, user_id),
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=None,
                status="none",
                current_period_end=None,
            )
            raise HTTPException(status_code=400, detail=e.user_message or "Your card was declined.")
        except Exception as e:
            logger.error(f"Error creating subscription for user {user_id}: {e}", exc_info=True)
            await store.user_repo.add_or_update_subscription(
                user_id=user_id,
                organization_id=await _billing_organization_id(store, user_id),
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=None,
                status="none",
                current_period_end=None,
            )
            raise HTTPException(status_code=400, detail="Could not create subscription")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscription error: {e}", exc_info=True)
        raise HTTPException(status_code=403, detail="Could not process subscription")

@subscriptions_router.post("/confirm", status_code=200)
async def confirm_subscription(
    organization_id: Optional[int] = Body(None, embed=True),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Re-read a subscription from Stripe after the browser passed a challenge.

    WHY THIS EXISTS RATHER THAN JUST WAITING FOR THE WEBHOOK

    Finishing 3D Secure moves the subscription to active, and Stripe does send
    customer.subscription.updated, which the webhook below already handles. But
    the webhook is a separate HTTP round trip on Stripe's schedule, and the
    browser asks "am I in?" the instant the popup closes. Reading the database
    at that moment usually still says 'incomplete', so the customer who just
    authenticated successfully gets bounced back to the payment screen and told
    to subscribe again.

    Stripe is the authority on whether the money moved, so this asks Stripe
    directly instead of racing its webhook. The webhook stays exactly as it is
    and remains the thing that catches every later change; this is only the
    synchronous answer to one question at one moment.

    Idempotent by construction: it writes whatever Stripe currently says. Two
    calls, or a call that crosses the webhook, converge on the same row.
    """
    user_id = user_data["user_id"]
    try:
        billing_org_id = await _billing_organization_id(store, user_id, organization_id)
        if billing_org_id is None:
            raise HTTPException(status_code=403, detail="No organization to bill.")
        await assert_organization_capability(
            store, user_id, billing_org_id, Capability.MANAGE_BILLING
        )

        subscription_data = await store.user_repo.get_subscription(user_id)
        if not subscription_data:
            raise HTTPException(status_code=404, detail="No subscription found")
        subscription, _ = subscription_data
        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Subscription ID is missing")

        fresh = await stripe.Subscription.retrieve_async(subscription_id)
        await store.user_repo.update_subscription(
            stripe_customer_id=subscription.get('stripe_customer_id'),
            stripe_subscription_id=subscription_id,
            status=fresh.status,
            current_period_end=_period_end(fresh),
        )
        logger.info("Confirmed subscription %s for user %s: %s", subscription_id, user_id, fresh.status)
        return {"subscription_status": fresh.status}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming subscription for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not confirm subscription")


@subscriptions_router.post("/setup-intent", status_code=200)
async def create_setup_intent(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """A secret the browser needs before it can save a replacement card.

    The card-update form calls stripe.confirmCardSetup(clientSecret), and
    nothing ever gave it one: the state was declared, never assigned, so the
    call was made with an empty string and failed on contact. Anybody whose
    subscription went past_due reached a form that could not work.

    A SetupIntent is the object that authorises storing a card for later, and it
    runs the same 3D Secure challenge a payment does, which is the other half of
    why this matters: replacing a card on an EU account needs authentication too.
    """
    user_id = user_data["user_id"]
    try:
        subscription_data = await store.user_repo.get_subscription(user_id)
        if not subscription_data:
            raise HTTPException(status_code=404, detail="No subscription found")
        subscription, _ = subscription_data
        stripe_customer_id = subscription.get('stripe_customer_id')
        if not stripe_customer_id:
            raise HTTPException(status_code=400, detail="No billing account on file")

        intent = await stripe.SetupIntent.create_async(
            customer=stripe_customer_id,
            usage="off_session",
        )
        return {"client_secret": intent.client_secret}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating setup intent for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not start card update")


# Route to update payment method
@subscriptions_router.post("/update-payment", status_code=200)
async def update_payment(
    payment_method: str = Body(..., embed=True),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription_data = await store.user_repo.get_subscription(user_id)
        if not subscription_data:
            raise HTTPException(status_code=404, detail="No subscription found")

        subscription, card_details = subscription_data
        stripe_customer_id = subscription.get('stripe_customer_id')
        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Subscription ID is missing")


        # Keep the id and the object apart. This rebound `payment_method` to the
        # retrieved object and then passed that object where the API takes an
        # id, so the attach and the modify were both built from the wrong thing.
        payment_method_id = payment_method
        card = await stripe.PaymentMethod.retrieve_async(payment_method_id)
        await stripe.PaymentMethod.attach_async(payment_method_id, customer=stripe_customer_id)
        await stripe.Subscription.modify_async(
            subscription_id, default_payment_method=payment_method_id
        )
        # A new card on a past_due subscription should also become the customer's
        # default, or the next invoice retries the card that already failed.
        await stripe.Customer.modify_async(
            stripe_customer_id,
            invoice_settings={'default_payment_method': payment_method_id},
        )

        # Read the status back from Stripe rather than echoing the row we just
        # read. Paying an outstanding invoice with the new card moves past_due
        # to active, and writing back the stale value put the customer's own
        # database row behind the truth.
        fresh = await stripe.Subscription.retrieve_async(subscription_id)
        await store.user_repo.update_subscription(
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=subscription_id,
            status=fresh.status,
            current_period_end=_period_end(fresh),
            card_last4=card.card.last4,
            card_type=card.card.brand,
            exp_month=card.card.exp_month,
            exp_year=card.card.exp_year
        )

        return {
            'success': True,
            'subscription_status': fresh.status,
            'card_last4': card.card.last4,
            'card_brand': card.card.brand,
            'card_exp_month': card.card.exp_month,
            'card_exp_year': card.card.exp_year,
        }

    except HTTPException:
        raise
    except stripe.error.CardError as e:
        logger.info(f"Card declined updating payment method for user {user_id}: {e.user_message or e}")
        raise HTTPException(status_code=400, detail=e.user_message or "Your card was declined.")
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error updating payment method for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not update payment method")

# Route to handle Stripe webhooks
@subscriptions_router.post("/webhook")
async def webhook(request: Request, store: RepositoryManager = Depends(get_store)):
    payload = await request.body()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        # event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        event = await asyncio.to_thread(stripe.Webhook.construct_event, payload, sig_header, endpoint_secret)

        event_type = event['type']
        data_object = event['data']['object']
        stripe_customer_id = data_object['customer']

        # Do not mutate subscription status based on invoice events.
        # Subscription status should be treated as authoritative from the subscription object itself.
        if event_type in {'invoice.payment_succeeded', 'invoice.payment_failed'}:
            logger.info(f"Ignoring invoice event for entitlement: {event_type}")
            return {"status": "ignored"}

        elif event_type == 'customer.subscription.updated':
            current_status = data_object['status']
            # _read, never .get. This handler used to call
            # event['data'].get('previous_attributes', {}) and
            # data_object.get('current_period_end'), both of which raise
            # AttributeError on a StripeObject. The only handlers below catch
            # signature and payload errors, so every subscription.updated event
            # returned 500 and Stripe retried it forever. Nothing about a
            # subscription going past_due or cancelled ever reached the
            # database: the row stayed 'active' and the organization kept full
            # access without paying. That is the leak, and it is the same
            # mistake as the seat sync.
            previous_status = _read(_read(event['data'], 'previous_attributes', {}) or {}, 'status')
            current_period_end_dt = _period_end(data_object)
            if previous_status and previous_status != current_status:
                logger.info(
                    "Subscription %s moved %s -> %s",
                    _read(data_object, 'id'), previous_status, current_status,
                )

            # Keyed by the subscription, not the customer. One person owning two
            # organizations gets one Stripe customer under the old lookup, and
            # two rows sharing a customer id made scalar_one_or_none raise
            # MultipleResultsFound: their webhooks broke entirely, and before
            # that the wrong organization's row could be updated.
            await store.user_repo.update_subscription(
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=_read(data_object, 'id'),
                status=current_status,
                current_period_end=current_period_end_dt
            )
            # Keep the recorded plan and seat allowance in step with Stripe.
            #
            # A plan change made in the Stripe dashboard, or a quantity Stripe
            # itself adjusted, would otherwise never reach the database: the
            # webhook only ever wrote status, so an organization upgraded to
            # Business kept being measured against Starter's ten included
            # seats.
            try:
                items = _read(_read(data_object, 'items', {}) or {}, 'data') or []
                stripe_price_id = items[0]['price']['id'] if items else None
                plan = plan_for_price_id(stripe_price_id)
                if plan:
                    await store.user_repo.update_subscription(
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=_read(data_object, 'id'),
                        status=current_status,
                        current_period_end=current_period_end_dt,
                        seats=plan.included_seats,
                        plan_key=plan.key,
                    )
                elif stripe_price_id:
                    logger.warning(
                        "Subscription %s uses price %s, which matches no configured plan",
                        _read(data_object, 'id'), stripe_price_id,
                    )
            except Exception as e:
                # Never fail the webhook over this: Stripe retries on a non-2xx
                # and the status update above has already landed.
                logger.error(f"Could not sync plan for {stripe_customer_id}: {e}", exc_info=True)

        elif event_type == 'customer.subscription.deleted':
            await store.user_repo.update_subscription_status(stripe_customer_id, "canceled")

        else:
            # Return 200 for unhandled events so Stripe doesn't retry.
            logger.info(f"Ignoring unhandled Stripe event type: {event_type}")
            return {"status": "ignored"}

        return {"status": "success"}
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    finally:
        # No sessions to manage as we're using the repository pattern now
        pass