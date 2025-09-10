from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body, status
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Union, Any
import stripe
import logging
import os
from pydantic import BaseModel, Field, validator
from enum import Enum
from dotenv import load_dotenv

from ..repositories.async_user_repository import AsyncUserRepository, SubscriptionStatus
from ..repositories.repository_manager import RepositoryManager, get_repository_manager
from ..utils.utils import get_user_id

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def handle_stripe_error(e: stripe.error.StripeError) -> HTTPException:
    """Helper function to handle Stripe errors consistently."""
    error_type = getattr(e, 'error', {}).get('type', 'stripe_error')
    error_message = getattr(e, 'user_message', str(e))
    logger.error(f"Stripe error ({error_type}): {error_message}", exc_info=True)
    
    # Map common Stripe errors to user-friendly messages
    error_map = {
        'card_error': 'There was an error processing your card. Please try again or use a different card.',
        'invalid_request_error': 'Invalid request. Please check your information and try again.',
        'api_connection_error': 'Unable to connect to our payment processor. Please try again later.',
        'api_error': 'An error occurred while processing your request. Please try again.',
        'authentication_error': 'Unable to authenticate with our payment processor. Please try again later.',
        'rate_limit_error': 'Too many requests. Please try again in a moment.',
        'validation_error': 'Validation error. Please check your information and try again.'
    }
    
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=error_map.get(error_type, error_message)
    )

# Initialize FastAPI router
subscriptions_router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# Stripe configuration
price_id = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = os.getenv('STRIPE_SECRET')
# Use consistent environment variable name for webhook secret
endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

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

    user = await store.user_repo.get_user_by_email(user_info['email'])
    if not user:
        logger.error(f"No user found with email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user.id

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

# Pydantic Models
class SubscriptionStatusEnum(str, Enum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"

class CardDetailsInput(BaseModel):
    last4: str = Field(..., min_length=4, max_length=4, description="Last 4 digits of the card")
    brand: str = Field(..., description="Card brand (e.g., 'visa', 'mastercard')")
    exp_month: int = Field(..., ge=1, le=12, description="Expiration month (1-12)")
    exp_year: int = Field(..., description="Expiration year (4 digits)")
    
    @validator('exp_year')
    def validate_exp_year(cls, v):
        current_year = datetime.now().year
        if v < current_year:
            raise ValueError('Card has expired')
        if v > current_year + 20:  # Cards are typically valid for up to 20 years
            raise ValueError('Invalid expiration year')
        return v
    stripe_payment_method_id: Optional[str] = Field(None, description="Stripe payment method ID")

class UpdateSubscriptionRequest(BaseModel):
    status: Optional[SubscriptionStatusEnum] = None
    current_period_end: Optional[datetime] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    card_details: Optional[CardDetailsInput] = None

class UpdateSubscriptionStatusRequest(BaseModel):
    status: SubscriptionStatusEnum
    user_id: Optional[int] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    current_period_end: Optional[datetime] = None
    card_details: Optional[CardDetailsInput] = None

class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    status: str
    current_period_end: Optional[datetime] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    card_details: Optional[Dict[str, Any]] = None

    class Config:
        orm_mode = True

# Dependency to get the repository
async def get_user_repo() -> AsyncUserRepository:
    repo_manager = get_repository_manager()
    return AsyncUserRepository(repo_manager)

def format_subscription_response(subscription_data: Optional[Dict]) -> Dict:
    """Format subscription response consistently across all endpoints.
    
    Handles both raw database rows and processed subscription data.
    """
    if not subscription_data:
        return {
            'subscription_status': 'none',
            'card_last4': None,
            'card_brand': None,
            'card_exp_month': None,
            'card_exp_year': None,
            'trial_end': None
        }
    
    # Extract card details from different possible locations
    card_details = subscription_data.get('card_details', {}) or {}
    if not card_details and 'card_type' in subscription_data:
        card_details = {
            'card_type': subscription_data.get('card_type'),
            'card_last4': subscription_data.get('card_last4'),
            'exp_month': subscription_data.get('exp_month'),
            'exp_year': subscription_data.get('exp_year')
        }
    
    # Handle trial_end from different possible locations
    trial_end = None
    trial_end_sources = [
        subscription_data.get('trial_end'),
        subscription_data.get('trial_ends_at'),
        subscription_data.get('current_period_end')  # Fallback to current_period_end if trial_end not available
    ]
    
    for ts in trial_end_sources:
        if ts:
            if isinstance(ts, (int, float)):
                trial_end = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                break
            elif isinstance(ts, datetime):
                trial_end = ts.isoformat()
                break
            elif isinstance(ts, str):
                trial_end = ts  # Assume it's already in ISO format
                break
    
    # Build the response
    response = {
        'subscription_status': subscription_data.get('status', 'none'),
        'card_last4': card_details.get('card_last4') or subscription_data.get('card_last4'),
        'card_brand': card_details.get('card_type') or subscription_data.get('card_brand') or subscription_data.get('card_type'),
        'card_exp_month': card_details.get('exp_month') or subscription_data.get('exp_month'),
        'card_exp_year': card_details.get('exp_year') or subscription_data.get('exp_year'),
        'trial_end': trial_end
    }
    
    # Add any additional fields that might be present
    for field in ['message', 'error', 'stripe_client_secret']:
        if field in subscription_data:
            response[field] = subscription_data[field]
    
    return response

# Route to get subscription status
@subscriptions_router.get("/status")
async def subscription_status(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        
        # Get subscription data
        subscription_data = await store.user_repo.get_user_subscription(user_id)
        
        if not subscription_data:
            return format_subscription_response({
                'status': 'none',
                'message': 'No subscription found'
            })
        
        # Format the response using our helper function
        response = format_subscription_response(subscription_data)
        
        # Add any additional metadata
        if 'trial_end' in subscription_data and subscription_data['trial_end']:
            trial_end = subscription_data['trial_end']
            if isinstance(trial_end, str):
                trial_end = datetime.fromisoformat(trial_end.replace('Z', '+00:00'))
            elif isinstance(trial_end, (int, float)):
                trial_end = datetime.fromtimestamp(trial_end, tz=timezone.utc)
                
            if isinstance(trial_end, datetime):
                response['trial_days_remaining'] = (trial_end - datetime.now(timezone.utc)).days
            
        return response
        
    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': 'Failed to retrieve subscription status'
        })

@subscriptions_router.get("/users/{user_id}/subscription", response_model=SubscriptionResponse)
async def get_user_subscription(
    user_id: int,
    repo: AsyncUserRepository = Depends(get_user_repo),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Return basic subscription info for a user.
    
    - **user_id**: The ID of the user to get subscription for
    """
    if user_data["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this resource"
        )
    
    subscription = await repo.get_user_subscription(user_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    return subscription

@subscriptions_router.get("/users/{user_id}/subscription/details", response_model=SubscriptionResponse)
async def get_subscription_with_card(
    user_id: int,
    repo: AsyncUserRepository = Depends(get_user_repo),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Return full subscription info including payment methods.
    
    - **user_id**: The ID of the user to get subscription details for
    """
    if user_data["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this resource"
        )
    
    subscription = await repo.get_subscription_with_card(user_id)
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    return subscription

@subscriptions_router.put("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: int,
    data: UpdateSubscriptionRequest,
    repo: AsyncUserRepository = Depends(get_user_repo),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Update a subscription's details.
    
    - **subscription_id**: The ID of the subscription to update
    - **data**: The subscription data to update
    """
    update_data = data.dict(exclude_unset=True)
    
    # Extract card details if present
    card_details = update_data.pop("card_details", None)
    if card_details:
        update_data.update({
            "card_last4": card_details.last4,
            "card_brand": card_details.brand,
            "exp_month": card_details.exp_month,
            "exp_year": card_details.exp_year,
            "stripe_payment_method_id": card_details.stripe_payment_method_id
        })
    
    async with repo._repository_manager.session_scope() as session:
        updated = await repo.update_subscription(subscription_id, update_data, session)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found"
            )
        return updated

@subscriptions_router.patch("/subscriptions/status", status_code=status.HTTP_200_OK)
async def update_subscription_status(
    data: UpdateSubscriptionStatusRequest,
    repo: AsyncUserRepository = Depends(get_user_repo),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Update a subscription's status.
    
    - **data**: The status update data
    """
    update_data = data.dict(exclude_unset=True)
    
    # Extract card details if present
    card_details = update_data.pop("card_details", None)
    if card_details:
        update_data.update({
            "card_last4": card_details.last4,
            "card_brand": card_details.brand,
            "exp_month": card_details.exp_month,
            "exp_year": card_details.exp_year,
            "stripe_payment_method_id": card_details.stripe_payment_method_id
        })
    
    if update_data.get("user_id") != user_data["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this resource"
        )
    
    async with repo._repository_manager.session_scope() as session:
        success = await repo.update_subscription_status(**update_data, session=session)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Failed to update subscription status"
            )
        return {"success": True, "message": "Subscription status updated successfully"}

# Keep existing Stripe integration routes below
# Route to start a trial
@subscriptions_router.post("/start-trial", status_code=status.HTTP_201_CREATED)
async def start_trial(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
    repo: AsyncUserRepository = Depends(get_user_repo)
):
    """
    Start a trial subscription for a user.
    
    - **payment_method**: Stripe payment method ID
    - **price_id**: Stripe price ID for the subscription
    """
    payment_method = request.get('payment_method')
    price_id = request.get('price_id')
    
    if not payment_method or not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment method and price ID are required"
        )
    
    if not price_id:
        raise HTTPException(status_code=400, detail="Price ID is required")
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]
        email = user_info.get('email')
        
        if not email:
            raise HTTPException(status_code=400, detail="Email is required")

        # Check if the user already has a subscription
        subscription = await store.user_repo.get_subscription(user_id)
        if subscription:
            # If subscription exists, check its status
            if subscription.get('status') == 'active':
                return {"message": "You already have an active subscription"}
            elif subscription.get('status') == 'trialing':
                return {"message": "You are already in a trial period"}

        # Create a new trial subscription in Stripe
        trial_end_dt = datetime.utcnow() + timedelta(days=7)  # 7 days from now
        trial_end_timestamp = int(trial_end_dt.timestamp())
        
        # Create a customer in Stripe if they don't exist
        stripe_customer_id = user_info.get('stripe_customer_id')
        if not stripe_customer_id:
            customer_data = {
                'email': email,
                'metadata': {
                    'user_id': str(user_id)
                }
            }
            
            # Only add payment method if provided (for trials that require a payment method)
            if payment_method:
                customer_data.update({
                    'payment_method': payment_method,
                    'invoice_settings': {
                        'default_payment_method': payment_method
                    }
                })
                
            customer = stripe.Customer.create(**customer_data)
            stripe_customer_id = customer.id
            
            # Save the customer ID to the user's profile
            await store.user_repo.update_user(user_id, {'stripe_customer_id': stripe_customer_id})
        else:
            customer = stripe.Customer.retrieve(user_info['stripe_customer_id'])

        # Create a subscription with trial
        subscription_data = {
            'customer': customer.id,
            'items': [{'price': price_id}],
            'trial_end': trial_end_timestamp,
            'payment_behavior': 'default_incomplete',
            'expand': ['latest_invoice.payment_intent']
        }
        
        # If payment method was provided, set it up for the subscription
        if payment_method:
            subscription_data.update({
                'default_payment_method': payment_method
            })
        
        subscription = stripe.Subscription.create(**subscription_data)

        # Get payment method details
        payment_method = None
        if hasattr(subscription, 'default_payment_method') and subscription.default_payment_method:
            payment_method = stripe.PaymentMethod.retrieve(subscription.default_payment_method)
        
        # Prepare subscription data for our repository
        current_period_end = datetime.fromtimestamp(subscription.current_period_end, timezone.utc)
        
        # Update or create subscription in database using our repository
        subscription_data = {
            'status': subscription.status,
            'current_period_end': current_period_end,
            'stripe_customer_id': stripe_customer_id,  # Use the customer ID we just created/retrieved
            'stripe_subscription_id': subscription.id,
            'stripe_payment_method_id': payment_method.id if payment_method else None,
        }
        
        if payment_method and hasattr(payment_method, 'card'):
            subscription_data.update({
                'card_last4': payment_method.card.last4,
                'card_brand': payment_method.card.brand,
                'exp_month': payment_method.card.exp_month,
                'exp_year': payment_method.card.exp_year,
            })
        
        # Use a transaction to ensure data consistency
        async with repo._repository_manager.session_scope() as session:
            try:
                # Update subscription status in our database
                success = await repo.update_subscription_status(
                    user_id=user_id,
                    **{k: v for k, v in subscription_data.items() if v is not None},
                    session=session
                )
                
                if not success:
                    raise Exception("Failed to update subscription status in database")
                
                # Get the updated subscription from our database
                db_subscription = await repo.get_subscription_with_card(user_id, session=session)
                
                # Commit the transaction
                await session.commit()
                
                # Prepare response
                client_secret = None
                if (hasattr(subscription, 'latest_invoice') and 
                    subscription.latest_invoice and 
                    hasattr(subscription.latest_invoice, 'payment_intent') and 
                    subscription.latest_invoice.payment_intent):
                    client_secret = subscription.latest_invoice.payment_intent.client_secret
                
                return {
                    'subscription_id': subscription.id,
                    'status': subscription.status,
                    'current_period_end': current_period_end.isoformat(),
                    'client_secret': client_secret,
                    'subscription': db_subscription
                }
                
            except Exception as db_error:
                await session.rollback()
                logger.error(f"Database error in start_trial: {str(db_error)}", exc_info=True)
                
                # Attempt to cancel the Stripe subscription if database update fails
                try:
                    stripe.Subscription.delete(subscription.id)
                except Exception as cancel_error:
                    logger.error(f"Failed to cancel subscription after database error: {str(cancel_error)}")
                
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An error occurred while processing your trial. Please try again."
                )

    except stripe.error.StripeError as e:
        raise handle_stripe_error(e)

    except Exception as e:
        logger.error(f"Unexpected error in start_trial: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred. Please try again later.")

# Route to cancel a subscription
@subscriptions_router.post("/cancel", status_code=200)
async def cancel_sub(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        
        # Get current subscription
        subscription = await store.user_repo.get_user_subscription(user_id)
        if not subscription or not subscription.get('stripe_subscription_id'):
            return format_subscription_response({
                'status': 'error',
                'error': 'No active subscription found'
            })
        
        if subscription.get('status') == 'canceled':
            return format_subscription_response({
                'status': 'canceled',
                'message': 'Subscription is already canceled'
            })
        
        stripe_sub_id = subscription['stripe_subscription_id']
        
        try:
            # Cancel the subscription at period end in Stripe
            stripe_sub = stripe.Subscription.modify(
                stripe_sub_id,
                cancel_at_period_end=True
            )
            
            # Update subscription in our database
            subscription_update = {
                'status': 'canceled',
                'current_period_end': datetime.fromtimestamp(
                    stripe_sub.current_period_end, 
                    tz=timezone.utc
                ) if hasattr(stripe_sub, 'current_period_end') else None
            }
            
            # Use add_or_update_subscription to handle the update
            await store.user_repo.add_or_update_subscription(user_id, subscription_update)
            
            # Get updated subscription data
            updated_sub = await store.user_repo.get_user_subscription(user_id)
            response = format_subscription_response(updated_sub)
            response['message'] = "Subscription will be canceled at the end of the billing period"
            return response
            
        except stripe.error.InvalidRequestError as e:
            if "No such subscription" in str(e):
                # If subscription doesn't exist in Stripe, mark as canceled locally
                await store.user_repo.add_or_update_subscription(user_id, {
                    'status': 'canceled',
                    'current_period_end': datetime.now(timezone.utc)
                })
                
                updated_sub = await store.user_repo.get_user_subscription(user_id)
                response = format_subscription_response(updated_sub)
                response['message'] = "Subscription was already canceled and removed from Stripe"
                return response
                
            raise handle_stripe_error(e)
            
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in cancel_sub: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': "An error occurred while canceling your subscription"
        })
            
    except Exception as e:
        logger.error(f"Unexpected error in cancel_sub: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': "An unexpected error occurred while canceling your subscription"
        })

# Route to create a subscription
@subscriptions_router.post("/subscribe")
async def create_subscription(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        # Get payment method ID from request
        payment_method_id = request.get('payment_method_id')
        if not payment_method_id:
            return format_subscription_response({
                'status': 'error',
                'error': "Payment method ID is required"
            })

        user_id = user_data["user_id"]
        email = user_data["email"]

        # Check if user already has a subscription
        existing_sub = await store.user_repo.get_user_subscription(user_id)
        if existing_sub and existing_sub.get('status') in ['active', 'trialing']:
            return format_subscription_response({
                'status': 'error',
                'error': "User already has an active subscription"
            })

        # Create or retrieve Stripe customer
        customer_id = None
        if existing_sub and existing_sub.get('stripe_customer_id'):
            customer_id = existing_sub['stripe_customer_id']
            # Update customer's default payment method
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=customer_id,
            )
            stripe.Customer.modify(
                customer_id,
                invoice_settings={
                    'default_payment_method': payment_method_id
                }
            )
        else:
            customer = stripe.Customer.create(
                email=email,
                payment_method=payment_method_id,
                invoice_settings={
                    'default_payment_method': payment_method_id
                }
            )
            customer_id = customer.id

        # Get payment method details
        payment_method = stripe.PaymentMethod.retrieve(payment_method_id)

        # Create subscription
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{
                'price': price_id,
            }],
            expand=['latest_invoice.payment_intent'],
            payment_behavior='default_incomplete',
            payment_settings={
                'save_default_payment_method': 'on_subscription',
            },
            off_session=True
        )

        # Prepare subscription data for database
        subscription_data = {
            'user_id': user_id,
            'stripe_customer_id': customer_id,
            'stripe_subscription_id': subscription.id,
            'status': subscription.status,
            'current_period_end': datetime.fromtimestamp(
                subscription.current_period_end, 
                tz=timezone.utc
            ) if hasattr(subscription, 'current_period_end') else None
        }

        # Add card details if available
        if hasattr(payment_method, 'card'):
            subscription_data.update({
                'card_last4': payment_method.card.last4,
                'card_brand': payment_method.card.brand,
                'card_exp_month': payment_method.card.exp_month,
                'card_exp_year': payment_method.card.exp_year,
                'stripe_payment_method_id': payment_method_id
            })

        # Save subscription to database
        await store.user_repo.upsert_subscription(subscription_data)

        # Get the updated subscription with all details
        subscription_data = await store.user_repo.get_user_subscription(user_id)

        # Format the response
        response = format_subscription_response(subscription_data)

        # Add payment intent client secret if available
        if (hasattr(subscription, 'latest_invoice') and 
            subscription.latest_invoice and
            hasattr(subscription.latest_invoice, 'payment_intent') and 
            subscription.latest_invoice.payment_intent):

            response.update({
                'requires_action': True,
                'payment_intent_client_secret': subscription.latest_invoice.payment_intent.client_secret
            })

        return response

    except stripe.error.CardError as e:
        logger.error(f"Card error: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': str(e)
        })
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': "An error occurred while processing your subscription"
        })
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': "An unexpected error occurred"
        })

# Route to update payment method
@subscriptions_router.post("/update-payment", status_code=200)
async def update_payment(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        payment_method_id = request.get('payment_method_id')
        
        if not payment_method_id:
            return format_subscription_response({
                'status': 'error',
                'error': 'Payment method ID is required'
            })
        
        # Get current subscription
        subscription = await store.user_repo.get_user_subscription(user_id)
        if not subscription or not subscription.get('stripe_customer_id'):
            return format_subscription_response({
                'status': 'error',
                'error': 'No active subscription found'
            })
        
        try:
            # Attach the payment method to the customer
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=subscription['stripe_customer_id']
            )
            
            # Set as default payment method
            stripe.Customer.modify(
                subscription['stripe_customer_id'],
                invoice_settings={
                    'default_payment_method': payment_method_id
                }
            )
            
            # Get payment method details
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
            
            # Update subscription with new payment method
            await store.user_repo.add_or_update_subscription(user_id, {
                'stripe_payment_method_id': payment_method_id,
                'card_last4': payment_method.card.last4,
                'card_brand': payment_method.card.brand,
                'exp_month': payment_method.card.exp_month,
                'exp_year': payment_method.card.exp_year
            })
            
            # Get updated subscription data
            updated_sub = await store.user_repo.get_user_subscription(user_id)
            response = format_subscription_response(updated_sub)
            response['message'] = "Payment method updated successfully"
            return response
            
        except stripe.error.CardError as e:
            logger.error(f"Card error in update_payment: {str(e)}", exc_info=True)
            return format_subscription_response({
                'status': 'error',
                'error': e.user_message or 'Your card was declined. Please try again with a different payment method.'
            })
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error in update_payment: {str(e)}", exc_info=True)
            return format_subscription_response({
                'status': 'error',
                'error': 'An error occurred while updating your payment method. Please try again.'
            })
            
    except Exception as e:
        logger.error(f"Unexpected error in update_payment: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': 'An unexpected error occurred while updating your payment method.'
        })

# Route to handle Stripe webhooks
@subscriptions_router.post("/webhook", status_code=200)
async def webhook(
    request: Request,
    store: RepositoryManager = Depends(get_store)
):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        # Invalid payload
        logger.error(f"Invalid payload in webhook: {str(e)}")
        return format_subscription_response({
            'status': 'error',
            'error': 'Invalid payload'
        })
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.error(f"Invalid signature in webhook: {str(e)}")
        return format_subscription_response({
            'status': 'error',
            'error': 'Invalid signature'
        })
    
    # Handle the event
    event_type = event['type']
    data = event['data']
    
    try:
        if event_type == 'payment_method.attached':
            payment_method = event['data']['object']  # contains a stripe.PaymentMethod
            customer_id = payment_method.customer
            
            try:
                # Get the subscription for this customer
                subscriptions = stripe.Subscription.list(customer=customer_id, limit=1)
                if subscriptions and len(subscriptions.data) > 0:
                    subscription = subscriptions.data[0]
                    
                    # Update the subscription with the new payment method
                    stripe.Subscription.modify(
                        subscription.id,
                        default_payment_method=payment_method.id
                    )
                    
                    # Update our database
                    await store.user_repo.add_or_update_subscription(
                        user_id=None,  # We'll look up by customer_id
                        subscription_data={
                            'stripe_customer_id': customer_id,
                            'stripe_payment_method_id': payment_method.id,
                            'card_last4': payment_method.card.last4,
                            'card_brand': payment_method.card.brand,
                            'exp_month': payment_method.card.exp_month,
                            'exp_year': payment_method.card.exp_year
                        }
                    )
                    
            except Exception as e:
                logger.error(f"Error processing payment_method.attached: {str(e)}", exc_info=True)
                raise
        
        elif event_type == 'customer.subscription.updated':
            subscription = event['data']['object']
            customer_id = subscription.customer
            
            # Update subscription status in our database
            subscription_data = {
                'stripe_customer_id': customer_id,
                'stripe_subscription_id': subscription.id,
                'status': subscription.status,
                'current_period_end': datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc)
            }
            
            # Handle trial end if it exists
            if hasattr(subscription, 'trial_end') and subscription.trial_end:
                subscription_data['trial_end'] = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
            
            # If subscription is canceled at period end, update status
            if getattr(subscription, 'cancel_at_period_end', False):
                subscription_data['status'] = 'canceled'
            
            await store.user_repo.add_or_update_subscription(
                user_id=None,  # Look up by customer_id
                subscription_data=subscription_data
            )
            
            logger.info(f"Updated subscription {subscription.id} for customer {customer_id}")
        
        elif event_type == 'customer.subscription.deleted':
            subscription = event['data']['object']
            customer_id = subscription.customer
            
            # Mark subscription as canceled in our database
            await store.user_repo.add_or_update_subscription(
                user_id=None,  # Look up by customer_id
                subscription_data={
                    'stripe_customer_id': customer_id,
                    'status': 'canceled',
                    'current_period_end': datetime.fromtimestamp(
                        subscription.current_period_end, 
                        tz=timezone.utc
                    ) if hasattr(subscription, 'current_period_end') else None,
                    'stripe_subscription_id': subscription.id
                }
            )
            
            logger.info(f"Marked subscription {subscription.id} as canceled for customer {customer_id}")
    
        # If we get here, the event was processed successfully
        return format_subscription_response({'status': 'success'})

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {str(e)}", exc_info=True)
        return format_subscription_response({
            'status': 'error',
            'error': 'Internal server error'
        })