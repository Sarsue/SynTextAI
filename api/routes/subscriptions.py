from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body
from fastapi.responses import JSONResponse
from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel
import stripe
import logging
import os
from dotenv import load_dotenv
from ..core.utils import get_user_id
from ..core.limits import resolve_entitlement
from ..core.plans import PLANS, get_plan, plan_for_price_id
from ..core.seats import seat_summary, sync_seats_to_stripe
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

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(authorization: str = Header(None), store: RepositoryManager = Depends(get_store)):
    if not authorization:
        logger.error("Missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    success, user_info = get_user_id(authorization)
    if not success:
        logger.error("Failed to authenticate user with token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}


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
    # Subscript access, not .get: a StripeObject routes attribute lookups
    # through __getattr__, so .get raises AttributeError instead of behaving
    # like a mapping. A dict is also accepted, which is what the webhook passes.
    def _read(obj, key):
        try:
            return obj[key]
        except Exception:
            return None

    items = _read(stripe_subscription, "items")
    data = _read(items, "data") if items is not None else None
    for item in data or []:
        value = _read(item, "current_period_end")
        if value:
            return datetime.utcfromtimestamp(value)

    value = _read(stripe_subscription, "current_period_end")
    return datetime.utcfromtimestamp(value) if value else None


async def _billing_organization_id(store: RepositoryManager, user_id: int) -> Optional[int]:
    """The organization this subscription belongs to.

    Subscriptions are read back by organization, so anything written without
    one leaves the tenant looking unpaid: the customer starts a trial and is
    immediately locked out of the app they just subscribed to. Owners and admins
    are the only roles that can be billing for a tenant.
    """
    memberships = await store.org_repo.get_memberships(user_id)
    for m in memberships:
        if m["role"] in ("owner", "admin"):
            return m["organization_id"]
    return None


# Route to get subscription status
@subscriptions_router.get("/status")
async def subscription_status(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription_data = await store.user_repo.get_subscription(user_id)

        # Entitlement is an organization property, not a personal one. A staff
        # member invited into a paid workspace has no subscription of their own
        # and must still be treated as entitled, otherwise the frontend sends
        # them through billing onboarding for an account that already pays.
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

        organization_id = await _billing_organization_id(store, user_id)
        if organization_id is None:
            raise HTTPException(
                status_code=403,
                detail="Your access is provided by your team's plan. Contact your workspace owner about billing.",
            )
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
   
        # Retrieve the payment method ID from the request
        payment_method_id = payment_method
        if not payment_method_id:
            logger.error(f"Request came without a valid payment method ID: {user_id}")
            raise HTTPException(status_code=400, detail="Payment method ID is missing")

        # If stripe_customer_id is still None, check if the customer exists in Stripe
        if not stripe_customer_id:
            # Look for an existing customer using the email
            existing_customers = await stripe.Customer.list_async(email=user_info.get('email'))
            if existing_customers.data:
                stripe_customer_id = existing_customers.data[0].id
                logger.info(f"Found existing Stripe customer: {stripe_customer_id}")
            else:
                # Create a new Stripe customer if no existing customer is found
                customer = await stripe.Customer.create_async(
                    description=f"Customer for user_id {user_id}",
                    email=user_info.get('email'),
                    name=user_info.get('name')
                )
                stripe_customer_id = customer.id
                logger.info(f"Created new Stripe customer: {stripe_customer_id}")

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

            # Create a new Stripe subscription
            created_subscription = await stripe.Subscription.create_async(
                customer=stripe_customer_id,
                items=[{'price': price_id, 'quantity': member_count}],
                default_payment_method=payment_method_id
            )

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


        payment_method = await stripe.PaymentMethod.retrieve_async(payment_method)
        await stripe.PaymentMethod.attach_async(payment_method, customer=stripe_customer_id)
        await  stripe.Subscription.modify_async(subscription_id, default_payment_method=payment_method)

        await store.user_repo.update_subscription(
            stripe_customer_id=stripe_customer_id,
            status=subscription.get("status"),  # Or retrieve status from Stripe if required
            current_period_end=subscription.get("current_period_end"),  # Already a datetime in repo
            card_last4=payment_method.card.last4,
            card_type=payment_method.card.brand,
            exp_month=payment_method.card.exp_month,
            exp_year=payment_method.card.exp_year
        )

        return {'success': True}

    except HTTPException:
        raise
    except stripe.error.CardError as e:
        logger.info(f"Card declined updating payment method for user {user_id}: {e.user_message or e}")
        raise HTTPException(status_code=400, detail=e.user_message or "Your card was declined.")
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
            previous_status = event['data'].get('previous_attributes', {}).get('status') # Keep this for potential future use or logging
            # Same relocation as above: on current API versions the period
            # lives on the subscription item, not the subscription.
            current_period_end_dt = _period_end(data_object)
            current_period_end = data_object.get('current_period_end')
            if current_period_end_dt is not None:
                pass
            elif isinstance(current_period_end, (int, float)):
                current_period_end_dt = datetime.utcfromtimestamp(current_period_end)
            elif isinstance(current_period_end, str):
                # Defensive: if someone stored ISO strings, keep compatible.
                try:
                    current_period_end_dt = datetime.fromisoformat(current_period_end)
                except Exception:
                    current_period_end_dt = None

            await store.user_repo.update_subscription(
                stripe_customer_id=stripe_customer_id,
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
                items = (data_object.get('items') or {}).get('data') or []
                stripe_price_id = items[0]['price']['id'] if items else None
                plan = plan_for_price_id(stripe_price_id)
                if plan:
                    await store.user_repo.update_subscription(
                        stripe_customer_id=stripe_customer_id,
                        status=current_status,
                        current_period_end=current_period_end_dt,
                        seats=plan.included_seats,
                        plan_key=plan.key,
                    )
                elif stripe_price_id:
                    logger.warning(
                        "Subscription %s uses price %s, which matches no configured plan",
                        data_object.get('id'), stripe_price_id,
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