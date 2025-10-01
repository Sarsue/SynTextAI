from datetime import datetime
from typing import Dict, Optional, Any, List, Union, cast
import logging
import os

import stripe
from dotenv import load_dotenv
from fastapi import (
    APIRouter, 
    Depends, 
    HTTPException, 
    Request, 
    Body, 
    status,
    Response
)
from fastapi.responses import JSONResponse
from pydantic import validator
from sqlalchemy.ext.asyncio import AsyncSession

from api.repositories.base_repository_manager import get_repository_manager
from api.repositories.async_user_repository import AsyncUserRepository
from api.repositories.domain_models import UserInDB, Subscription
from api.models.subscription_schemas import (
    CardDetailsResponse,
    SubscriptionResponse,
    SubscriptionCreate,
    SubscriptionUpdate,
    WebhookEvent
)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# Load environment variables
load_dotenv()

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
STRIPE_PRICE_ID = os.getenv('STRIPE_PRICE_ID')
stripe.api_key = STRIPE_SECRET_KEY

# Type aliases
JSONType = Dict[str, Any]

# Use models from api.models.subscription_schemas

async def get_current_user(
    request: Request,
    authorization: str = Header(..., description="Bearer token")
) -> Dict[str, Any]:
    """Get current user from authorization token"""
    if not authorization:
        logger.error("Missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get repository manager
    repo_manager = await get_repository_manager()
    user_repo = repo_manager.user_repo

    try:
        # Get user info from token
        success, user_info = get_user_id(authorization)
        if not success or not user_info.get('email'):
            logger.error("Failed to authenticate user with token")
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Get user from database
        user = await user_repo.get_by_email(user_info['email'])
        if not user:
            logger.error(f"No user found for email: {user_info['email']}")
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"Authenticated user_id: {user.id}")
        return {
            "user_id": user.id,
            "user_info": user_info,
            "user_repo": user_repo
        }
    except Exception as e:
        logger.error(f"Error in get_current_user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while authenticating user"
        )

@router.get("/status", response_model=SubscriptionResponse)
async def get_subscription_status(
    current_user: Dict = Depends(get_current_user)
) -> SubscriptionResponse:
    """Get the current user's subscription status"""
    try:
        user_id = current_user["user_id"]
        user_repo = current_user["user_repo"]
        
        # Get subscription from database
        subscription_data = await user_repo._get_subscription_base(user_id, include_payment_methods=True)
        
        if not subscription_data:
            return SubscriptionResponse(
                subscription_status='none'
            )
        
        return SubscriptionResponse(
            subscription_status=subscription_data.get('status', 'none'),
            card_last4=subscription_data.get('card_details', {}).get('last4') if subscription_data.get('card_details') else None,
            card_brand=subscription_data.get('card_details', {}).get('brand') if subscription_data.get('card_details') else None,
            card_exp_month=subscription_data.get('card_details', {}).get('exp_month') if subscription_data.get('card_details') else None,
            card_exp_year=subscription_data.get('card_details', {}).get('exp_year') if subscription_data.get('card_details') else None,
            trial_end=subscription_data.get('trial_end')
        )
        
    except Exception as e:
        logger.error(f"Error in get_subscription_status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while getting subscription status"
        )

@router.post("/start-trial", status_code=201)
async def start_trial(
    current_user: Dict = Depends(get_current_user)
):
    """Start a trial subscription for the current user"""
    try:
        user_id = current_user["user_id"]
        user_info = current_user["user_info"]
        user_repo = current_user["user_repo"]
        
        # Check if user already has a subscription
        existing_sub = await user_repo._get_subscription_base(user_id)
        if existing_sub and existing_sub.get('status') in ['active', 'trialing']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Trial or active subscription already exists"
            )
        
        # Create or get Stripe customer
        customer = None
        if 'email' in user_info:
            existing_customers = stripe.Customer.list(email=user_info['email'])
            if existing_customers.data:
                customer = existing_customers.data[0]
        
        if not customer:
            customer = stripe.Customer.create(
                email=user_info.get('email'),
                name=user_info.get('name', '')
            )
        
        # Create trial subscription
        trial_sub = stripe.Subscription.create(
            customer=customer.id,
            items=[{'price': STRIPE_PRICE_ID}],
            trial_period_days=14,
            payment_behavior='default_incomplete'
        )
        
        # Save to database
        async with user_repo.session_scope() as session:
            # Update or create subscription
            subscription_data = {
                'user_id': user_id,
                'stripe_customer_id': customer.id,
                'stripe_subscription_id': trial_sub.id,
                'status': 'trialing',
                'current_period_end': datetime.fromtimestamp(trial_sub.current_period_end),
                'trial_end': datetime.fromtimestamp(trial_sub.trial_end)
            }
            
            # Use the repository method to update or create subscription
            subscription = await user_repo._get_subscription_by_criteria(
                user_id=user_id, 
                session=session
            )
            
            if subscription:
                for key, value in subscription_data.items():
                    setattr(subscription, key, value)
                session.add(subscription)
            else:
                subscription = Subscription(**subscription_data)
                session.add(subscription)
            
            await session.commit()
            await session.refresh(subscription)
        
        return {
            'message': 'Trial started successfully',
            'subscription_id': trial_sub.id,
            'status': 'trialing',
            'trial_end': datetime.fromtimestamp(trial_sub.trial_end).isoformat(),
            'current_period_end': datetime.fromtimestamp(trial_sub.current_period_end).isoformat()
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in start_trial: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in start_trial: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while starting trial"
        )

@router.post("/create", status_code=201)
async def create_subscription(
    subscription_data: SubscriptionCreate,
    current_user: Dict = Depends(get_current_user)
):
    """Create a new subscription"""
    try:
        user_id = current_user["user_id"]
        user_info = current_user["user_info"]
        user_repo = current_user["user_repo"]
        
        # Get or create Stripe customer
        customer = None
        if 'email' in user_info:
            existing_customers = stripe.Customer.list(email=user_info['email'])
            if existing_customers.data:
                customer = existing_customers.data[0]
        
        if not customer and 'email' in user_info:
            customer = stripe.Customer.create(
                email=user_info.get('email'),
                name=user_info.get('name', ''),
                payment_method=subscription_data.payment_method_id,
                invoice_settings={
                    'default_payment_method': subscription_data.payment_method_id
                }
            )
        
        # Create subscription in Stripe
        subscription = await user_repo.create_subscription(
            user_id=user_id,
            subscription_data={
                **subscription_data.dict(),
                'customer_id': customer.id if customer else None
            }
        )
        
        return {
            'status': 'success',
            'subscription_id': subscription.id,
            'client_secret': getattr(getattr(subscription, 'latest_invoice', None), 'payment_intent', {}).get('client_secret') if hasattr(subscription, 'latest_invoice') else None,
            'status': getattr(subscription, 'status', 'active')
        }
        
    except stripe.error.CardError as e:
        logger.error(f"Card error in create_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in create_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in create_subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating subscription"
        )

@router.put("/{subscription_id}", response_model=Dict[str, Any])
async def update_subscription(
    subscription_id: str,
    update_data: SubscriptionUpdate,
    current_user: Dict = Depends(get_current_user)
):
    """Update an existing subscription"""
    try:
        user_id = current_user["user_id"]
        user_repo = current_user["user_repo"]
        
        async with user_repo.session_scope() as session:
            # Get subscription from database
            subscription = await user_repo._get_subscription_by_criteria(
                user_id=user_id,
                session=session
            )
            
            if not subscription:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Subscription not found"
                )
            
            # Update subscription in Stripe
            update_params = {}
            if update_data.cancel_at_period_end is not None:
                update_params['cancel_at_period_end'] = update_data.cancel_at_period_end
            
            if update_data.status:
                update_params['status'] = update_data.status
            
            if update_params:
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    **update_params
                )
            
            # Update in database
            for key, value in update_data.dict(exclude_none=True).items():
                setattr(subscription, key, value)
            
            session.add(subscription)
            await session.commit()
            
            return {
                'message': 'Subscription updated successfully',
                'subscription_id': subscription.id,
                'status': subscription.status,
                'cancel_at_period_end': subscription.cancel_at_period_end
            }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in update_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in update_subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating subscription"
        )

@router.post("/cancel", status_code=200)
async def cancel_subscription(
    current_user: Dict = Depends(get_current_user)
):
    """Cancel the current user's subscription"""
    try:
        user_id = current_user["user_id"]
        user_repo = current_user["user_repo"]
        
        async with user_repo.session_scope() as session:
            # Get subscription from database
            subscription = await user_repo._get_subscription_by_criteria(
                user_id=user_id,
                session=session
            )
            
            if not subscription:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No active subscription found"
                )
            
            # Cancel subscription in Stripe
            if subscription.stripe_subscription_id:
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    cancel_at_period_end=True
                )
            
            # Update subscription status in database
            subscription.status = 'canceled'
            subscription.canceled_at = datetime.utcnow()
            
            session.add(subscription)
            await session.commit()
            
            return {"message": "Subscription will be canceled at the end of the billing period"}
            
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in cancel_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in cancel_subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while canceling subscription"
        )

@router.post("/update-payment", status_code=200)
async def update_payment_method(
    payment_method: Dict[str, Any] = Body(...),
    current_user: Dict = Depends(get_current_user)
):
    """Update the payment method for the current user's subscription"""
    try:
        user_id = current_user["user_id"]
        user_repo = current_user["user_repo"]
        
        async with user_repo.session_scope() as session:
            # Get subscription from database
            subscription = await user_repo._get_subscription_by_criteria(
                user_id=user_id,
                session=session
            )
            
            if not subscription:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No active subscription found"
                )
            
            if not subscription.stripe_customer_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No customer ID found for subscription"
                )
            
            # Attach payment method to customer
            payment_method_obj = stripe.PaymentMethod.attach(
                payment_method.get('id'),
                customer=subscription.stripe_customer_id
            )
            
            # Update customer's default payment method
            stripe.Customer.modify(
                subscription.stripe_customer_id,
                invoice_settings={
                    'default_payment_method': payment_method_obj.id
                }
            )
            
            # Update or create card details in database
            card_data = {
                'subscription_id': subscription.id,
                'stripe_payment_method_id': payment_method_obj.id,
                'card_type': payment_method_obj.card.brand,
                'last4': payment_method_obj.card.last4,
                'exp_month': payment_method_obj.card.exp_month,
                'exp_year': payment_method_obj.card.exp_year
            }
            
            # Check if card details already exist
            existing_card = await session.execute(
                select(CardDetails)
                .where(CardDetails.subscription_id == subscription.id)
            )
            existing_card = existing_card.scalar_one_or_none()
            
            if existing_card:
                for key, value in card_data.items():
                    setattr(existing_card, key, value)
                session.add(existing_card)
            else:
                session.add(CardDetails(**card_data))
            
            await session.commit()
            
            return {"message": "Payment method updated successfully"}
            
    except stripe.error.CardError as e:
        logger.error(f"Card error in update_payment_method: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error in update_payment_method: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in update_payment_method: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating payment method"
        )

@router.post("/webhook", status_code=200)
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid payload: {str(e)}")
        return Response(status_code=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {str(e)}")
        return Response(status_code=400)
    
    # Get repository manager
    repo_manager = await get_repository_manager()
    user_repo = repo_manager.user_repo
    
    async with user_repo.session_scope() as session:
        # Handle the event
        if event['type'] == 'payment_intent.succeeded':
            payment_intent = event['data']['object']
            logger.info(f"PaymentIntent was successful: {payment_intent.id}")
        
        elif event['type'] == 'payment_method.attached':
            payment_method = event['data']['object']
            logger.info(f"PaymentMethod was attached: {payment_method.id}")
        
        elif event['type'] == 'customer.subscription.updated':
            subscription = event['data']['object']
            await handle_subscription_updated(subscription, user_repo, session)
        
        elif event['type'] == 'customer.subscription.deleted':
            subscription = event['data']['object']
            await handle_subscription_deleted(subscription, user_repo, session)
        
        await session.commit()
    
    return Response(status_code=200)

async def handle_subscription_updated(
    subscription: Dict[str, Any],
    user_repo: AsyncUserRepository,
    session: AsyncSession
):
    """Handle subscription updated webhook event"""
    try:
        # Get subscription from database
        subscription_obj = await user_repo._get_subscription_by_criteria(
            stripe_subscription_id=subscription['id'],
            session=session
        )
        
        if not subscription_obj:
            logger.warning(f"No subscription found for Stripe ID: {subscription['id']}")
            return
        
        # Update subscription status
        subscription_obj.status = subscription['status']
        subscription_obj.current_period_end = datetime.fromtimestamp(subscription['current_period_end'])
        subscription_obj.cancel_at_period_end = subscription['cancel_at_period_end']
        
        session.add(subscription_obj)
        logger.info(f"Updated subscription {subscription['id']} status to {subscription['status']}")
        
    except Exception as e:
        logger.error(f"Error in handle_subscription_updated: {str(e)}", exc_info=True)
        raise

async def handle_subscription_deleted(
    subscription: Dict[str, Any],
    user_repo: AsyncUserRepository,
    session: AsyncSession
):
    """Handle subscription deleted webhook event"""
    try:
        # Get subscription from database
        subscription_obj = await user_repo._get_subscription_by_criteria(
            stripe_subscription_id=subscription['id'],
            session=session
        )
        
        if not subscription_obj:
            logger.warning(f"No subscription found for Stripe ID: {subscription['id']}")
            return
        
        # Update subscription status
        subscription_obj.status = 'canceled'
        subscription_obj.canceled_at = datetime.utcnow()
        
        session.add(subscription_obj)
        logger.info(f"Marked subscription {subscription['id']} as canceled")
        
    except Exception as e:
        logger.error(f"Error in handle_subscription_deleted: {str(e)}", exc_info=True)
        raise

# Export the router
subscriptions_router = router

# Import models here to avoid circular imports
from api.models.orm_models import Subscription, CardDetails
