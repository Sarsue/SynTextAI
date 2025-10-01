from fastapi import APIRouter, Depends, HTTPException, Header, Request, Body, status
from fastapi.responses import JSONResponse
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Union, Any, cast
import stripe
import logging
import os
from pydantic import BaseModel, Field, validator
from enum import Enum
from dotenv import load_dotenv

from ..repositories import RepositoryManager
from ..dependencies import authenticate_user
from ..app import limiter

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

# Get repository manager dependency
async def get_repo_manager() -> RepositoryManager:
    repo_manager = await get_repository_manager()
    if not repo_manager._repos_initialized:
        await repo_manager._initialize_repositories()
    return repo_manager

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

async def format_subscription_response(
    subscription_data: Optional[Dict],
    repo_manager: RepositoryManager
) -> Optional[Dict]:
    """Format subscription response consistently across all endpoints.
    
    Handles both raw database rows and processed subscription data.
    """
    if not subscription_data:
        return None
        
    # Convert SQLAlchemy model to dict if needed
    if hasattr(subscription_data, '_asdict'):
        subscription_data = subscription_data._asdict()
    elif hasattr(subscription_data, '__dict__'):
        subscription_data = subscription_data.__dict__
        # Remove SQLAlchemy instance state
        subscription_data.pop('_sa_instance_state', None)
    
    # Get user details if user_id is present
    if 'user_id' in subscription_data and subscription_data['user_id']:
        user = await repo_manager.user_repo.get_user(subscription_data['user_id'])
        if user:
            subscription_data['user_email'] = user.email
    
    # Convert datetime objects to ISO format
    for key, value in subscription_data.items():
        if isinstance(value, datetime):
            subscription_data[key] = value.isoformat()
    
    # Ensure all expected fields are present
    result = {
        'id': subscription_data.get('id'),
        'user_id': subscription_data.get('user_id'),
        'status': subscription_data.get('status'),
        'current_period_end': subscription_data.get('current_period_end'),
        'stripe_customer_id': subscription_data.get('stripe_customer_id'),
        'stripe_subscription_id': subscription_data.get('stripe_subscription_id'),
        'created_at': subscription_data.get('created_at'),
        'updated_at': subscription_data.get('updated_at'),
        'card_details': subscription_data.get('card_details')
    }
    
    return {k: v for k, v in result.items() if v is not None}

# Route to get subscription status
@subscriptions_router.get("/subscription/details", response_model=Dict[str, Any])
async def get_subscription_with_card(
    user_data: Dict = Depends(authenticate_user)
):
    """
    Return full subscription info including payment methods for the current user.
    """
    try:
        repo_manager = user_data['repo_manager']
        subscription_repo = await repo_manager.subscription_repo
        subscription = await subscription_repo.get_subscription_by_user_id(user_id=user_data["internal_user_id"], include_payment_methods=True)
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No subscription found for this user"
            )
        return subscription
    except Exception as e:
        logger.error(f"Error getting subscription with card: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching subscription details"
        )

@subscriptions_router.get("/subscription", response_model=SubscriptionResponse)
async def get_user_subscription(
    user_data: Dict = Depends(authenticate_user)
):
    """
    Return basic subscription info for the current user.
    """
    try:
        repo_manager = user_data['repo_manager']
        subscription_repo = await repo_manager.subscription_repo
        subscription = await subscription_repo.get_subscription_by_user_id(user_id=user_data["internal_user_id"])
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No subscription found for this user"
            )
        return subscription
    except Exception as e:
        logger.error(f"Error getting subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching subscription"
        )

@subscriptions_router.put("/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: int,
    data: UpdateSubscriptionRequest,
    user_data: Dict = Depends(authenticate_user)
):
    """
    Update a subscription's details.
    
    - **subscription_id**: The ID of the subscription to update
    - **data**: The subscription data to update
    """
    try:
        repo_manager = user_data['repo_manager']
        subscription_repo = await repo_manager.subscription_repo
        
        # Get the subscription
        subscription = await subscription_repo.get_subscription_by_id(subscription_id)
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found"
            )
            
        # Check if user owns this subscription
        if subscription['user_id'] != user_data['internal_user_id']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this subscription"
            )
            
        # Update subscription data
        update_data = data.dict(exclude_unset=True)
        if 'card_details' in update_data:
            card_data = update_data.pop('card_details')
            update_data.update({
                'card_last4': card_data.last4,
                'card_brand': card_data.brand,
                'exp_month': card_data.exp_month,
                'exp_year': card_data.exp_year,
                'stripe_payment_method_id': card_data.stripe_payment_method_id
            })
            
        # Update subscription in database
        await subscription_repo.update_subscription(
            subscription_id=subscription_id,
            **update_data
        )
        
        # Get updated subscription data
        updated_sub = await subscription_repo.get_subscription_by_id(subscription_id)
        if not updated_sub:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update subscription"
            )
            
        return updated_sub
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating subscription"
        )

@subscriptions_router.patch("/subscriptions/status", status_code=status.HTTP_200_OK, response_model=Dict[str, Any])
async def update_subscription_status(
    data: UpdateSubscriptionStatusRequest,
    user_data: Dict = Depends(authenticate_user)
):
    """
    Update a subscription's status.
    
    - **data**: The status update data
    """
    try:
        repo_manager = user_data['repo_manager']
        user_repo = await repo_manager.user_repo
        update_data = data.dict(exclude_unset=True, exclude_none=True)
        
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
        
        # Check if the user is authorized to update this subscription
        if update_data.get("user_id") and update_data["user_id"] != user_data["internal_user_id"] and not user_data.get("is_admin", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this subscription"
            )
        
        # Update the subscription status
        success = await user_repo.update_subscription_status(**update_data)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Failed to update subscription status"
            )
            
        return {"success": True, "message": "Subscription status updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating subscription status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update subscription status: {str(e)}"
        )

# Keep existing Stripe integration routes below
# Route to start a trial
@subscriptions_router.post("/start-trial", status_code=status.HTTP_201_CREATED)
async def start_trial(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Start a trial subscription for a user.
    
    - **payment_method**: Stripe payment method ID (optional for free trials)
    - **price_id**: Stripe price ID for the subscription
    """
    try:
        payment_method = request.get('payment_method')
        price_id = request.get('price_id')
        
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Price ID is required"
            )
            
        user_id = user_data["internal_user_id"]
        user_info = user_data["user_info"]
        email = user_info.get('email')
        
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required"
            )

        # Get user repository and check for existing subscription
        user_repo = await repo_manager.user_repo
        subscription = await user_repo.get_user_subscription(user_id)
        if subscription:
            # If subscription exists, check its status
            if subscription.status == 'active':
                return {"message": "You already have an active subscription"}
            elif subscription.status == 'trialing':
                return {"message": "You are already in a trial period"}

        # Create a new trial subscription in Stripe
        trial_end_dt = datetime.utcnow() + timedelta(days=7)  # 7 days from now
        trial_end_timestamp = int(trial_end_dt.timestamp())
        
        # Create a customer in Stripe if they don't exist
        stripe_customer_id = user_info.get('stripe_customer_id')
        customer_data = {
            'email': email,
            'metadata': {'user_id': str(user_id)}
        }
        
        # Add payment method if provided
        if payment_method:
            customer_data.update({
                'payment_method': payment_method,
                'invoice_settings': {
                    'default_payment_method': payment_method
                }
            })
        
        # Create or update customer in Stripe
        if not stripe_customer_id:
            customer = stripe.Customer.create(**customer_data)
            stripe_customer_id = customer.id
            
            # Save the customer ID to the user's profile
            await repo_manager.user_repo.update_user(
                user_id, 
                {'stripe_customer_id': stripe_customer_id}
            )
        else:
            # Update existing customer with new payment method if provided
            if payment_method:
                stripe.Customer.modify(
                    stripe_customer_id,
                    **customer_data
                )
        
        # Create trial subscription in Stripe
        subscription_data = {
            'customer': stripe_customer_id,
            'items': [{'price': price_id}],
            'trial_end': trial_end_timestamp,
            'expand': ['latest_invoice.payment_intent']
        }
        
        # Add payment behavior for trials that require a payment method
        if payment_method:
            subscription_data.update({
                'payment_behavior': 'default_incomplete',
                'payment_settings': {'save_default_payment_method': 'on_subscription'}
            })
        
        subscription = stripe.Subscription.create(**subscription_data)
        
        # Save subscription to database
        subscription_data = {
            'user_id': user_id,
            'status': 'trialing',
            'stripe_subscription_id': subscription.id,
            'stripe_customer_id': stripe_customer_id,
            'current_period_end': datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc)
        }
        
        await repo_manager.user_repo.create_or_update_subscription(subscription_data)
        
        # Format the response
        response_data = {
            'status': 'success',
            'subscription_id': subscription.id,
            'subscription_status': subscription.status,
            'trial_end': trial_end_timestamp,
            'client_secret': (
                subscription.latest_invoice.payment_intent.client_secret 
                if hasattr(subscription, 'latest_invoice') and 
                   hasattr(subscription.latest_invoice, 'payment_intent')
                else None
            )
        }
        
        return response_data

    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Webhook signature verification failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature"
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in webhook: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in webhook: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the webhook"
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error starting trial: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment processing error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error starting trial: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while starting your trial"
        )

# Route to cancel a subscription
@subscriptions_router.post("/cancel", status_code=status.HTTP_200_OK)
async def cancel_sub(
    user_data: Dict = Depends(authenticate_user)
):
    """
    Cancel the current user's subscription at the end of the billing period.
    
    This will schedule the subscription for cancellation at the end of the current
    billing period. The user will retain access until that time.
    """
    try:
        repo_manager = user_data['repo_manager']
        user_repo = await repo_manager.user_repo
        
        # Get the subscription using the internal user ID from auth context
        subscription = await user_repo.get_subscription(
            user_id=user_data['internal_user_id']
        )
        if not subscription or not subscription.get('stripe_subscription_id'):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active subscription found"
            )
        
        if subscription.status == 'canceled':
            return {
                'status': 'canceled',
                'message': 'Subscription is already canceled',
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None
            }
        
        stripe_sub_id = subscription.stripe_subscription_id
        
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
            
            # Update subscription in database
            await user_repo.update_subscription(
                user_id=user_data['internal_user_id'],
                **subscription_update
            )
            
            # Get updated subscription data
            updated_sub = await user_repo.get_subscription(
                user_id=user_data['internal_user_id']
            )
            response = updated_sub  # Already formatted by the repository
            response['message'] = "Subscription will be canceled at the end of the billing period"
            return response
            
        except stripe.error.InvalidRequestError as e:
            if "No such subscription" in str(e):
                # If subscription doesn't exist in Stripe, mark as canceled locally
                await user_repo.update_subscription(
                    user_id=user_data['internal_user_id'],
                    status='canceled',
                    current_period_end=datetime.utcnow()
                )
                
                # Get the updated subscription
                updated_sub = await user_repo.get_subscription(
                    user_id=user_data['internal_user_id']
                )
                response = updated_sub  # Already formatted by the repository
                response['message'] = "Subscription was already canceled and removed from Stripe"
                return response
                
            raise handle_stripe_error(e)
            
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in cancel_sub: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An error occurred while canceling your subscription"
        )
            
    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_sub: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while canceling your subscription"
        )

# Route to create a subscription
@subscriptions_router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_subscription(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Create a new subscription for the current user.
    
    - **payment_method_id**: Stripe payment method ID (required)
    - **price_id**: Stripe price ID for the subscription (required)
    """
    try:
        # Validate required fields
        payment_method_id = request.get('payment_method_id')
        price_id = request.get('price_id')
        
        if not payment_method_id or not price_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment method ID and price ID are required"
            )

        user_id = user_data["internal_user_id"]
        email = user_data.get("email")
        
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User email is required"
            )

        repo_manager = user_data['repo_manager']
        user_repo = await repo_manager.user_repo
        
        # Check if user already has a subscription
        existing_sub = await user_repo.get_subscription(user_id=user_id)
        if existing_sub and existing_sub.status in ['active', 'trialing']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already has an active subscription"
            )

        try:
            # Create or retrieve Stripe customer
            customer_id = None
            if existing_sub and existing_sub.stripe_customer_id:
                customer_id = existing_sub.stripe_customer_id
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

            # Create subscription in our database
            subscription_data = {
                'user_id': user_id,
                'status': 'active',
                'stripe_customer_id': customer_id,
                'stripe_subscription_id': subscription.id,
                'current_period_end': datetime.fromtimestamp(
                    subscription.current_period_end,
                    tz=timezone.utc
                ) if hasattr(subscription, 'current_period_end') else None,
                'plan_id': price_id,
                'card_last4': payment_method.card.last4 if hasattr(payment_method, 'card') else None,
                'card_brand': payment_method.card.brand if hasattr(payment_method, 'card') else None,
                'exp_month': payment_method.card.exp_month if hasattr(payment_method, 'card') else None,
                'exp_year': payment_method.card.exp_year if hasattr(payment_method, 'card') else None,
                'stripe_payment_method_id': payment_method_id
            }
            
            subscription = await user_repo.create_subscription(**subscription_data)

            # Get the updated subscription with all details
            subscription_data = await user_repo.get_user_subscription(user_id)

            # Format the response
            response = await format_subscription_response(subscription_data, repo_manager)

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
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error in create_subscription: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment processing error: {str(e)}"
            )
            
        except HTTPException:
            raise
            
        except Exception as e:
            logger.error(f"Unexpected error in create_subscription: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while creating your subscription"
            )

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
@subscriptions_router.post("/update-payment-method", status_code=status.HTTP_200_OK)
async def update_payment(
    request: Dict = Body(...),
    user_data: Dict = Depends(authenticate_user)
):
    """
    Update the payment method for the current user's subscription.
    
    - **payment_method_id**: The Stripe payment method ID to use for future payments (required)
    """
    try:
        user_id = user_data["internal_user_id"]
        payment_method_id = request.get('payment_method_id')
        
        if not payment_method_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment method ID is required"
            )
        
        repo_manager = user_data['repo_manager']
        user_repo = await repo_manager.user_repo
        
        # Get current subscription
        subscription = await user_repo.get_subscription(user_id=user_id)
        if not subscription or not subscription.stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active subscription found"
            )
        
        try:
            # Attach the payment method to the customer
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=subscription.stripe_customer_id
            )
            
            # Set as default payment method
            stripe.Customer.modify(
                subscription.stripe_customer_id,
                invoice_settings={
                    'default_payment_method': payment_method_id
                }
            )
            
            # Get payment method details
            payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
            
            # Update subscription with new payment method
            update_data = {
                'stripe_payment_method_id': payment_method_id
            }
            
            if hasattr(payment_method, 'card'):
                update_data.update({
                    'card_last4': payment_method.card.last4,
                    'card_brand': payment_method.card.brand,
                    'card_exp_month': payment_method.card.exp_month,
                    'card_exp_year': payment_method.card.exp_year
                })
            
            await user_repo.update_subscription(
                user_id=user_id,
                **update_data
            )
            
            # Get updated subscription data
            updated_sub = await user_repo.get_user_subscription(user_id)
            response = await format_subscription_response(updated_sub, repo_manager)
            response['message'] = "Payment method updated successfully"
            return response
            
        except stripe.error.CardError as e:
            logger.error(f"Card error in update_payment: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=e.user_message or "Your card was declined. Please try again with a different payment method."
            )
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error in update_payment: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An error occurred while updating your payment method. Please try again."
            )
            
    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(f"Unexpected error in update_payment: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating your payment method."
        )

# Define supported webhook events with their purposes
SUPPORTED_EVENTS = {
    'payment_method.attached': 'Update payment method details',
    'customer.subscription.updated': 'Handle subscription changes',
    'customer.subscription.deleted': 'Handle subscription cancellation',
    'customer.subscription.trial_will_end': 'Handle upcoming trial end',
    'invoice.payment_succeeded': 'Handle successful payments',
    'invoice.payment_failed': 'Handle payment failures',
    'invoice.upcoming': 'Notify about upcoming charges',
    'customer.subscription.created': 'Handle new subscriptions'
}

# Subscription status transitions for validation
ALLOWED_STATUS_TRANSITIONS = {
    'trialing': ['active', 'past_due', 'canceled', 'unpaid'],
    'active': ['past_due', 'canceled', 'unpaid'],
    'past_due': ['active', 'canceled', 'unpaid'],
    'unpaid': ['canceled', 'active'],
    'canceled': []  # Final state
}

# Route to handle Stripe webhooks
@subscriptions_router.post("/webhook", status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")  # Rate limiting
async def webhook(
    request: Request
):
    """Handle Stripe webhook events with idempotency and detailed logging.
    
    This endpoint processes various Stripe webhook events related to subscriptions
    and payment methods. It's designed to be idempotent, meaning duplicate events
    will be detected and handled gracefully without side effects.
    
    Returns:
        JSON response with status and details about the processed event
    """
    # Get raw payload and signature
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    if not sig_header:
        logger.error("Missing Stripe-Signature header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "code": "MISSING_SIGNATURE",
                "message": "Missing Stripe-Signature header"
            }
        )
    
    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            endpoint_secret,
            tolerance=300  # 5 minutes tolerance for clock drift
        )
        
        # Log the event type for debugging
        logger.info(f"Processing Stripe webhook: {event.type}")
        
        # Create a new repository manager for the webhook
        repo_manager = RepositoryManager()
        await repo_manager.initialize()
        logger.info(f"Processing webhook: {event['type']} (ID: {event['id']})")
        
        # Get the user repository
        user_repo = await repo_manager.user_repo
        
        # Check if event type is supported
        if event['type'] not in SUPPORTED_EVENTS:
            logger.warning(f"Unsupported event type: {event['type']}")
            return {
                "status": "success", 
                "message": "Unsupported event type",
                "event_id": event['id'],
                "event_type": event['type']
            }
            
        # Handle different event types
        if event_type == 'customer.subscription.updated':
            subscription = event['data']['object']
            customer_id = subscription['customer']
            subscription_id = subscription.get('id', 'unknown')
            
            # Log subscription update details
            logger.info(f"Processing subscription update for customer {customer_id} (Subscription: {subscription_id})")
            
            # Get current subscription from DB
            current_sub = await user_repo.get_subscription_by_customer_id(customer_id)
            
            if current_sub:
                # Check if this is a duplicate event by comparing with current state
                current_period_end = current_sub.current_period_end
                subscription_period_end = subscription.get('current_period_end')
                
                # Check if this is a duplicate update
                if (current_period_end and 
                    subscription_period_end and 
                    current_period_end.timestamp() == subscription_period_end and
                    current_sub.status == subscription.get('status')):
                    
                    logger.info(f"Skipping duplicate subscription update for customer {customer_id} (Status: {subscription['status']})")
                    return {
                        "status": "success", 
                        "message": "Duplicate event - no changes detected",
                        "event_id": event_id,
                        "event_type": event_type
                    }
                
                # Log status transitions
                if current_sub.status != subscription.get('status'):
                    logger.info(f"Subscription status changed from {current_sub.status} to {subscription['status']} for customer {customer_id}")
                
                # Handle trial end
                if (current_sub.status == 'trialing' and 
                    subscription['status'] == 'active' and
                    'trial_end' in subscription and 
                    subscription['trial_end'] is not None and
                    subscription['trial_end'] <= datetime.now(timezone.utc).timestamp()):
                    
                    logger.info(f"Trial ended for customer {customer_id}")
                    # Trigger any trial end notifications or actions
            
            # Prepare update data
            update_data = {
                'stripe_customer_id': customer_id,
                'status': subscription['status'],
                'stripe_subscription_id': subscription_id,
                'current_period_end': datetime.fromtimestamp(subscription['current_period_end'], timezone.utc)
            }
            
            # Include trial end if it exists
            if 'trial_end' in subscription and subscription['trial_end'] is not None:
                update_data['trial_end'] = datetime.fromtimestamp(subscription['trial_end'], timezone.utc)
            
            # Update subscription in database
            logger.debug(f"Updating subscription with data: {json.dumps(update_data, default=str)}")
            
            # Use update_subscription with proper parameters
            if current_sub:  # Only update if we found a subscription
                await user_repo.update_subscription(
                    user_id=current_sub.user_id,
                    **{k: v for k, v in update_data.items() if k != 'user_id'}
                )
            logger.info(f"Successfully updated subscription {subscription_id} for customer {customer_id}")
            
        elif event_type == 'customer.subscription.deleted':
            subscription = event['data']['object']
            customer_id = subscription['customer']
            subscription_id = subscription.get('id', 'unknown')
            
            logger.info(f"Processing subscription deletion for customer {customer_id} (Subscription: {subscription_id})")
            
            # Check if already canceled
            current_sub = await user_repo.get_subscription_by_customer_id(customer_id)
            if current_sub and current_sub.status == 'canceled':
                logger.info(f"Subscription already marked as canceled for customer {customer_id}")
                return {
                    "status": "success",
                    "message": "Subscription already canceled",
                    "event_id": event['id'],
                    "event_type": event['type']
                }
            
            # Mark subscription as canceled
            logger.debug(f"Marking subscription {subscription_id} as canceled for customer {customer_id}")
            
            # Use update_subscription with proper parameters
            update_data = {
                'stripe_customer_id': customer_id,
                'status': 'canceled',
                'stripe_subscription_id': subscription_id,
                'cancelled_at': datetime.now(timezone.utc)
            }
            
            if current_sub:  # Only update if we found a subscription
                await user_repo.update_subscription(
                    user_id=current_sub.user_id,
                    **update_data
                )
            logger.info(f"Successfully marked subscription {subscription_id} as canceled for customer {customer_id}")
            
        elif event_type == 'payment_method.attached':
            payment_method = event['data']['object']
            customer_id = payment_method['customer']
            payment_method_id = payment_method.get('id', 'unknown')
            
            logger.info(f"Processing payment method attached for customer {customer_id} (Payment Method: {payment_method_id})")
            
            # Check if this is a card payment method
            if payment_method['type'] != 'card' or not payment_method.get('card'):
                logger.info(f"Skipping non-card payment method: {payment_method.get('type')}")
                return {
                    "status": "success",
                    "message": "Non-card payment method skipped",
                    "event_id": event_id,
                    "event_type": event_type
                }
            
            # Check if this is a duplicate update
            current_sub = await user_repo.get_subscription_by_customer_id(customer_id)
            card = payment_method['card']
            
            if (current_sub and 
                current_sub.stripe_payment_method_id == payment_method_id and
                current_sub.card_last4 == card['last4'] and
                str(current_sub.card_exp_month) == str(card['exp_month']) and
                str(current_sub.card_exp_year) == str(card['exp_year'])):
                
                logger.info(f"Skipping duplicate payment method update for customer {customer_id}")
                return {
                    "status": "success",
                    "message": "Payment method already up to date",
                    "event_id": event['id'],
                    "event_type": event['type']
                }
            
            # Update payment method in database
            logger.debug(f"Updating payment method for customer {customer_id} with card ending in {card['last4']}")
            
            # Prepare update data
            update_data = {
                'stripe_customer_id': customer_id,
                'card_last4': card['last4'],
                'card_brand': card['brand'],
                'card_exp_month': card['exp_month'],
                'card_exp_year': card['exp_year'],
                'stripe_payment_method_id': payment_method_id
            }
            
            if current_sub:  # Only update if we found a subscription
                await user_repo.update_subscription(
                    user_id=current_sub.user_id,
                    **update_data
                )
            logger.info(f"Successfully updated payment method for customer {customer_id}")
            
            # If this is a payment method update after a failed payment
            if current_sub and current_sub.status in ['past_due', 'unpaid']:
                logger.info(f"Processing payment method update for subscription in {current_sub.status} status")
                
                try:
                    # Get the most recent open invoice
                    invoices = stripe.Invoice.list(
                        customer=customer_id,
                        limit=1,
                        status='open'
                    )
                    
                    if invoices.data:
                        invoice = invoices.data[0]
                        logger.info(f"Found open invoice {invoice.id} for customer {customer_id}")
                        
                        # Pay the invoice with the new payment method
                        stripe.Invoice.pay(invoice.id, payment_method=payment_method_id)
                        logger.info(f"Paid outstanding invoice {invoice.id} for customer {customer_id}")
                        
                        # Update subscription status if needed
                        if current_sub.status != 'active':
                            await user_repo.update_subscription(
                                user_id=current_sub.user_id,
                                status='active'
                            )
                            logger.info(f"Updated subscription status to active for customer {customer_id}")
                    else:
                        logger.info(f"No open invoices found for customer {customer_id}")
                        
                except stripe.error.StripeError as e:
                    error_msg = f"Failed to process payment for customer {customer_id}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    # Don't fail the webhook - we'll log the error and continue
        
        # Log successful processing
        logger.info(f"Successfully processed webhook: {event['type']} (ID: {event['id']})")
        return {
            "status": "success",
            "event_id": event['id'],
            "event_type": event['type']
        }
        
    except stripe.error.SignatureVerificationError as e:
        error_id = str(uuid.uuid4())
        error_msg = f"Invalid webhook signature (Error ID: {error_id}): {str(e)}"
        logger.warning(error_msg)
        logger.debug(f"Request headers: {dict(request.headers)}")
        logger.debug(f"Request body (first 1000 chars): {payload.decode('utf-8', errors='replace')[:1000]}")
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "code": "INVALID_SIGNATURE",
                "message": "Invalid webhook signature",
                "error_id": error_id,
                "details": "The webhook signature verification failed. Please verify your webhook secret and try again."
            }
        )
        
    except stripe.error.StripeError as e:
        error_id = str(uuid.uuid4())
        error_msg = f"Stripe error processing webhook (Error ID: {error_id}): {str(e)}"
        logger.error(error_msg, exc_info=True)
        logger.debug(f"Request headers: {dict(request.headers)}")
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "code": "STRIPE_ERROR",
                "message": "Error processing webhook",
                "error_id": error_id,
                "details": f"An error occurred while processing the webhook: {str(e)}"
            }
        )
        
    except Exception as e:
        error_id = str(uuid.uuid4())
        error_msg = f"Unexpected error processing webhook (Error ID: {error_id}): {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "error_id": error_id,
                "details": "An unexpected error occurred while processing the webhook. Please try again later."
            }
        )