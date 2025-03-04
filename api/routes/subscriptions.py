from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body
from fastapi.responses import JSONResponse
from datetime import datetime
from typing import Dict, Optional
import stripe
import logging
import os
from dotenv import load_dotenv
from utils import get_user_id
from docsynth_store import DocSynthStore

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
subscriptions_router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# Stripe configuration
price_id = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = os.getenv('STRIPE_SECRET')
endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(authorization: str = Header(None), store: DocSynthStore = Depends(get_store)):
    if not authorization:
        logger.error("Missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    success, user_info = get_user_id(authorization)
    if not success:
        logger.error("Failed to authenticate user with token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = store.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Route to get subscription status
@subscriptions_router.get("/status")
async def subscription_status(
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription = store.get_subscription(user_id)
        
        if not subscription:
            return {
                'subscription_status': 'none',
                'card_last4': None,
                'card_brand': None,
                'card_exp_month': None,
                'card_exp_year': None,
                'trial_end': None
            }
        
        # Prepare subscription data to return
        response = {
            'subscription_status': subscription["status"],
            'card_last4': subscription["card_last4"],
            'card_brand': subscription["card_brand"],
            'card_exp_month': subscription["exp_month"],
            'card_exp_year': subscription["exp_year"],
            'trial_end': subscription["trial_end"]  
        }
        
        return response
    except Exception as e:
        logger.error(f"Error in subscription_status: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal error occurred")

# Route to start a trial
@subscriptions_router.post("/start-trial", status_code=201)
async def start_trial(
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]

        # Check if the user already has a subscription
        subscription = store.get_subscription(user_id)
        if subscription:
            # If subscription exists, check its status
            if subscription.get('status') == 'active':
                logger.error(f"Request came from an already active subscription: {user_id}")
                raise HTTPException(status_code=400, detail="Active subscription already exists")
            else:
                # If subscription exists but is not active, handle the logic for reactivating or restarting the trial
                pass
        else:
            # No subscription found, start a trial
            stripe_customer_id = None

            # Create a new Stripe customer if needed
            existing_customers = stripe.Customer.list(email=user_info.get('email'))
            if existing_customers.data:
                stripe_customer_id = existing_customers.data[0].id
                logger.info(f"Found existing Stripe customer: {stripe_customer_id}")
            else:
                # Create a new Stripe customer if no existing customer is found
                customer = stripe.Customer.create(
                    description=f"Customer for user_id {user_id}",
                    email=user_info.get('email'),
                    name=user_info.get('name')
                )
                stripe_customer_id = customer.id
                logger.info(f"Created new Stripe customer: {stripe_customer_id}")

            # Create a trial subscription for the user
            created_subscription = stripe.Subscription.create(
                customer=stripe_customer_id,
                items=[{'price': price_id}],
                trial_period_days=30,  # Adjust to your trial period duration
            )

            # Store the subscription in the database with the 'trial' status
            store.add_or_update_subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=created_subscription.id,
                status=created_subscription["status"],
                current_period_end=datetime.utcfromtimestamp(created_subscription.current_period_end),
                trial_end=datetime.utcfromtimestamp(created_subscription.trial_end),  # Pass trial_end
                card_last4=None,  # No card details at the start of the trial
                card_type=None,
                exp_month=None,
                exp_year=None
            )

            return {
                'message': 'Trial started successfully',
                'subscription_status': created_subscription["status"],
                'trial_end': datetime.utcfromtimestamp(created_subscription.trial_end)  # Correct trial_end value
            }

    except Exception as e:
        logger.error(f"Error starting trial: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Route to cancel a subscription
@subscriptions_router.post("/cancel", status_code=200)
async def cancel_sub(
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription_status = store.get_subscription(user_id)
        if not subscription_status:
            raise HTTPException(status_code=404, detail="No subscription found")

        subscription_id = subscription_status.get('stripe_subscription_id')
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Subscription ID is missing")

        cancellation_result = stripe.Subscription.delete(subscription_id)
        store.update_subscription_status(
            subscription_status['stripe_customer_id'],
            cancellation_result['status']
        )
    
        return {
            'subscription_status': cancellation_result['status'],
            'card_last4': subscription_status["card_last4"],
            'card_brand': subscription_status["card_brand"],
            'card_exp_month': subscription_status["exp_month"],
            'card_exp_year': subscription_status["exp_year"]
        }

    except Exception as e:
        raise HTTPException(status_code=403, detail=str(e))

# Route to create a subscription
@subscriptions_router.post("/subscribe", status_code=201)
async def create_subscription(
    payment_method: str = Body(..., embed=True),
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]

        # Check if user already has a subscription
        subscription = store.get_subscription(user_id)
        if subscription:
            # If subscription exists, get the customer ID from it
            stripe_customer_id = subscription.get('stripe_customer_id')
            if subscription.get('status') == 'active':
                logger.error(f"Request came from an already active subscription: {user_id}")
                raise HTTPException(status_code=400, detail="Active subscription already exists")
        else:
            # If no subscription exists, stripe_customer_id is None
            stripe_customer_id = None
        
        # Retrieve the payment method ID from the request
        payment_method_id = payment_method
        if not payment_method_id:
            logger.error(f"Request came without a valid payment method ID: {user_id}")
            raise HTTPException(status_code=400, detail="Payment method ID is missing")

        # If stripe_customer_id is still None, check if the customer exists in Stripe
        if not stripe_customer_id:
            # Look for an existing customer using the email
            existing_customers = stripe.Customer.list(email=user_info.get('email'))
            if existing_customers.data:
                stripe_customer_id = existing_customers.data[0].id
                logger.info(f"Found existing Stripe customer: {stripe_customer_id}")
            else:
                # Create a new Stripe customer if no existing customer is found
                customer = stripe.Customer.create(
                    description=f"Customer for user_id {user_id}",
                    email=user_info.get('email'),
                    name=user_info.get('name')
                )
                stripe_customer_id = customer.id
                logger.info(f"Created new Stripe customer: {stripe_customer_id}")

        try:
            # Attach the payment method to the customer
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=stripe_customer_id
            )

            # Set the payment method as the default for the customer
            stripe.Customer.modify(
                stripe_customer_id,
                invoice_settings={'default_payment_method': payment_method_id}
            )

            # Create a new Stripe subscription
            created_subscription = stripe.Subscription.create(
                customer=stripe_customer_id,
                items=[{'price': price_id}],
                default_payment_method=payment_method_id
            )
            
            # Store the subscription in the database
            store.add_or_update_subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=created_subscription.id,
                status=created_subscription.status,
                current_period_end=datetime.utcfromtimestamp(created_subscription.current_period_end),
                card_last4=payment_method.card.last4,
                card_type=payment_method.card.brand,
                exp_month=payment_method.card.exp_month,
                exp_year=payment_method.card.exp_year
            )

            return {
                'message': 'Subscription created successfully',
                "subscription_status": created_subscription.status, 
                'card_last4': payment_method.card.last4,
                'card_brand': payment_method.card.brand,
                'card_exp_month': payment_method.card.exp_month,
                'card_exp_year': payment_method.card.exp_year
            }

        except Exception as e:
            # Handle card errors
            error_msg = str(e)
            store.add_or_update_subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=None,
                status="none",
                current_period_end=None,
            )
            raise HTTPException(status_code=400, detail=error_msg)

    except Exception as e:
        logger.error(f"Subscription error: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))

# Route to update payment method
@subscriptions_router.post("/update-payment", status_code=200)
async def update_payment(
    payment_method: str = Body(..., embed=True),
    user_data: Dict = Depends(authenticate_user),
    store: DocSynthStore = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        subscription = store.get_subscription(user_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No subscription found")

        stripe_customer_id = subscription.get('stripe_customer_id')
        subscription_id = subscription.get('stripe_subscription_id')
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Subscription ID is missing")

        payment_method = stripe.PaymentMethod.retrieve(payment_method)
        stripe.PaymentMethod.attach(payment_method, customer=stripe_customer_id)
        stripe.Subscription.modify(subscription_id, default_payment_method=payment_method)

        store.update_subscription(
            stripe_customer_id=stripe_customer_id,
            status=subscription["status"],  # Or retrieve status from Stripe if required
            current_period_end=datetime.utcfromtimestamp(subscription["current_period_end"]),  # Retrieve current period end from Stripe if needed
            card_last4=payment_method.card.last4,
            card_type=payment_method.card.brand,
            exp_month=payment_method.card.exp_month,
            exp_year=payment_method.card.exp_year
        )

        return {'success': True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Route to handle Stripe webhooks
@subscriptions_router.post("/webhook")
async def webhook(request: Request, store: DocSynthStore = Depends(get_store)):
    payload = await request.body()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)

        event_type = event['type']
        data_object = event['data']['object']
        stripe_customer_id = data_object['customer']

        if event_type == 'invoice.payment_succeeded':
            store.update_subscription_status(stripe_customer_id, "active")

        elif event_type == 'invoice.payment_failed':
            store.update_subscription_status(stripe_customer_id, "card_declined")

        elif event_type == 'customer.subscription.updated':
            status = data_object['status']
            current_period_end = data_object['current_period_end']
            store.update_subscription(stripe_customer_id, status, current_period_end)

        elif event_type == 'customer.subscription.deleted':
            store.update_subscription_status(stripe_customer_id, "canceled")

        else:
            raise HTTPException(status_code=400, detail="Unhandled event")

        return {"status": "success"}

    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")