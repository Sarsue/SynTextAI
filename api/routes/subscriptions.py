from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import logging
import os

import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status, Response, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.subscription_schemas import (
    CardDetailsResponse,
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
    WebhookEvent
)
from ..repositories import RepositoryManager, AsyncUserRepository
from ..dependencies import get_repository_manager
from ..repositories.domain_models import UserInDB
from ..middleware.auth import get_current_user

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])

# Load environment variables
load_dotenv()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")

# Get repository manager for subscription operations
async def get_subscription_repo() -> RepositoryManager:
    """
    Get the repository manager instance for subscription operations.
    
    Returns:
        RepositoryManager: The repository manager instance
    """
    from api.dependencies import get_repository_manager
    return await get_repository_manager()

@router.get("/status", response_model=SubscriptionResponse)
async def get_subscription_status(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
) -> SubscriptionResponse:
    """
    Get the current user's subscription status
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        SubscriptionResponse: The user's subscription status
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Get user repository
            user_repo = await repo_manager.user_repo
            
            # Get user with subscription
            user = await user_repo.get_user_by_id(user.id, session=session)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
                
            # Get subscription from Stripe if exists
            subscription = None
            if user.stripe_subscription_id:
                try:
                    subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
                except stripe.error.StripeError as e:
                    logger.error(f"Error retrieving Stripe subscription: {str(e)}")
                    # Continue without subscription data if there's an error with Stripe
            
            # Determine subscription status
            is_active = False
            is_trialing = False
            trial_days_remaining = 0
            
            if user.subscription_status == "active":
                is_active = True
            elif user.subscription_status == "trialing":
                is_trialing = True
                if user.trial_ends_at:
                    trial_days_remaining = max(0, (user.trial_ends_at - datetime.utcnow()).days)
            
            return SubscriptionResponse(
                user_id=user.id,
                email=user.email,
                subscription_status=user.subscription_status,
                is_active=is_active,
                is_trialing=is_trialing,
                trial_days_remaining=trial_days_remaining,
                current_period_end=user.current_period_end,
                plan_id=user.plan_id,
                plan_name=getattr(user, 'plan_name', None),
                stripe_customer_id=user.stripe_customer_id,
                stripe_subscription_id=user.stripe_subscription_id,
                card_last4=getattr(user, 'card_last4', None),
                card_brand=getattr(user, 'card_brand', None)
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving subscription status"
        )

@router.post("/start-trial", status_code=201)
async def start_trial(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
):
    """
    Start a trial subscription for the current user
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict with trial status and details
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            # Get user repository
            user_repo = await repo_manager.user_repo
            
            # Get user with current data
            current_user = await user_repo.get_user_by_id(user.id, session=session)
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
                
            # Check if user already has a subscription
            if current_user.subscription_status in ["active", "trialing"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User already has an active subscription or trial"
                )
                
            # Set trial period (14 days from now)
            trial_days = 14
            trial_ends_at = datetime.utcnow() + timedelta(days=trial_days)
            
            # Update user with trial information
            update_data = {
                "subscription_status": "trialing",
                "trial_ends_at": trial_ends_at,
                "plan_id": "trial",
                "plan_name": "Free Trial",
                "updated_at": datetime.utcnow()
            }
            
            # Save changes
            await user_repo.update_user(current_user.id, update_data, session=session)
            
            # Commit the transaction
            await session.commit()
            
            return {
                'message': 'Trial started successfully',
                'subscription_id': subscription.id,
                'status': 'trialing',
                'trial_end': datetime.fromtimestamp(subscription.trial_end).isoformat(),
                'current_period_end': datetime.fromtimestamp(subscription.current_period_end).isoformat()
            }
        
    except HTTPException:
        raise
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

@router.post("", status_code=201)
async def create_subscription(
    subscription_data: SubscriptionCreate,
    request: Request,
    _user: UserInDB = Depends(get_current_user)
):
    """
    Create a new subscription
    
    Args:
        subscription_data: Subscription creation data including payment method
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict with subscription details including client secret for payment confirmation
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Get user with current data
            current_user = await user_repo.get_user_by_id(user.id, session=session)
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Check if user already has an active subscription
            if current_user.stripe_subscription_id:
                try:
                    existing_sub = stripe.Subscription.retrieve(current_user.stripe_subscription_id)
                    if existing_sub.status not in ['canceled', 'incomplete_expired']:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="User already has an active subscription"
                        )
                except stripe.error.InvalidRequestError:
                    # Subscription not found in Stripe, can proceed
                    pass
            
            # Get or create Stripe customer
            customer = None
            if current_user.email:
                try:
                    existing_customers = stripe.Customer.list(email=current_user.email)
                    if existing_customers.data:
                        customer = existing_customers.data[0]
                except stripe.error.StripeError as e:
                    logger.error(f"Error fetching Stripe customer: {str(e)}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Error processing payment information"
                    )
            
            if not customer and current_user.email:
                try:
                    customer = stripe.Customer.create(
                        email=current_user.email,
                        name=f"{current_user.first_name} {current_user.last_name}".strip() or None,
                        payment_method=subscription_data.payment_method_id,
                        invoice_settings={
                            'default_payment_method': subscription_data.payment_method_id
                        }
                    )
                except stripe.error.StripeError as e:
                    logger.error(f"Error creating Stripe customer: {str(e)}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Error creating payment account"
                    )
            
            if not customer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Could not process payment information"
                )
            
            # Create subscription in Stripe
            try:
                subscription = stripe.Subscription.create(
                    customer=customer.id,
                    items=[{
                        'price': STRIPE_PRICE_ID,
                    }],
                    payment_behavior='default_incomplete',
                    payment_settings={'save_default_payment_method': 'on_subscription'},
                    expand=['latest_invoice.payment_intent'],
                    trial_period_days=14 if not subscription_data.skip_trial else None
                )
            except stripe.error.StripeError as e:
                logger.error(f"Error creating Stripe subscription: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
            
            # Update user with subscription info
            update_data = {
                'stripe_customer_id': customer.id,
                'stripe_subscription_id': subscription.id,
                'subscription_status': 'incomplete',
                'plan_id': 'pro_monthly',
                'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
                'trial_ends_at': datetime.fromtimestamp(subscription.trial_end) if hasattr(subscription, 'trial_end') else None,
                'updated_at': datetime.utcnow()
            }
            
            await user_repo.update_user(current_user.id, update_data, session=session)
            
            # Commit the transaction
            await session.commit()
            
            return {
                'subscription_id': subscription.id,
                'client_secret': subscription.latest_invoice.payment_intent.client_secret,
                'status': subscription.status,
                'current_period_end': subscription.current_period_end,
                'trial_end': getattr(subscription, 'trial_end', None)
            }
        
    except HTTPException:
        raise
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

@router.put("/{subscription_id}", status_code=200)
async def update_subscription(
    subscription_id: str,
    update_data: SubscriptionUpdate,
    request: Request,
    _user: UserInDB = Depends(get_current_user)
):
    """
    Update an existing subscription
    
    Args:
        subscription_id: The ID of the subscription to update
        update_data: The fields to update
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict with update status and details
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Get the user with current data
            current_user = await user_repo.get_user_by_id(user.id, session=session)
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Verify the subscription belongs to the user
            if not current_user.stripe_subscription_id or current_user.stripe_subscription_id != subscription_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to update this subscription"
                )
            
            try:
                # Update subscription in Stripe
                stripe_subscription = stripe.Subscription.modify(
                    subscription_id,
                    **update_data.dict(exclude_unset=True)
                )
            except stripe.error.InvalidRequestError as e:
                logger.error(f"Invalid request to Stripe API: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
            
            # Prepare update data for user
            user_update_data = {
                'subscription_status': stripe_subscription.status,
                'current_period_end': datetime.fromtimestamp(stripe_subscription.current_period_end),
                'cancel_at_period_end': stripe_subscription.cancel_at_period_end,
                'updated_at': datetime.utcnow()
            }
            
            # Update user in database
            await user_repo.update_user(
                user.id,
                user_update_data,
                session=session
            )
            
            # Commit the transaction
            await session.commit()
            
            return {
                'status': 'success',
                'subscription_id': subscription_id,
                'updated_fields': list(update_data.dict(exclude_unset=True).keys())
            }
        
    except HTTPException:
        raise
    except stripe.error.CardError as e:
        logger.error(f"Card error in update_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
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
    request: Request,
    _user: UserInDB = Depends(get_current_user)
):
    """
    Cancel the current user's subscription
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict with cancellation status and details
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Get the user with current data
            current_user = await user_repo.get_user_by_id(user.id, session=session)
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Check if user has an active subscription
            if not current_user.stripe_subscription_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No active subscription found"
                )
            
            # Cancel subscription in Stripe immediately
            try:
                canceled_subscription = stripe.Subscription.delete(
                    current_user.stripe_subscription_id,
                    invoice_now=True,
                    prorate=True
                )
                
                # Update user in database
                update_data = {
                    'subscription_status': 'canceled',
                    'canceled_at': datetime.utcnow(),
                    'plan_id': None,
                    'plan_name': None,
                    'updated_at': datetime.utcnow()
                }
                
                await user_repo.update_user(
                    user.id,
                    update_data,
                    session=session
                )
                
                # Commit the transaction
                await session.commit()
                
                return {
                    'status': 'success',
                    'message': 'Subscription canceled successfully',
                    'subscription_id': current_user.stripe_subscription_id,
                    'canceled_at': update_data['canceled_at'].isoformat()
                }
                
            except stripe.error.InvalidRequestError as e:
                logger.error(f"Invalid request to Stripe API: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid subscription"
                )
            except stripe.error.StripeError as e:
                logger.error(f"Stripe error in cancel_subscription: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in cancel_subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while canceling subscription"
        )

@router.post("/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Handle Stripe webhook events
    
    This endpoint receives webhook events from Stripe and processes them asynchronously.
    It validates the webhook signature and then processes the event in the background.
    
    Args:
        request: The incoming HTTP request containing the webhook event
        background_tasks: FastAPI's background tasks for async processing
        
    Returns:
        Response with status code 200 if successful, or an error status code if validation fails
    """
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload, 
            sig_header, 
            STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.error(f"Invalid payload: {str(e)}")
        return Response(status_code=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {str(e)}")
        return Response(status_code=400)
    
    # Process the event in the background
    background_tasks.add_task(process_stripe_webhook_event, event)
    
    # Return 200 immediately to acknowledge receipt of the event
    return Response(status_code=200)

async def process_stripe_webhook_event(event: Dict[str, Any]):
    """
    Process a Stripe webhook event asynchronously
    
    This function handles the actual processing of webhook events in the background.
    It's called by the webhook endpoint to process events without blocking the response.
    
    Args:
        event: The Stripe event to process
    """
    # Get repository manager
    repo_manager = await get_repository_manager()
    
    try:
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Log the event type for debugging
            event_type = event['type']
            logger.info(f"Processing Stripe webhook event: {event_type}")
            
            # Handle the event based on its type
            if event_type == 'payment_intent.succeeded':
                payment_intent = event['data']['object']
                logger.info(f"PaymentIntent succeeded: {payment_intent.id}")
            
            elif event_type == 'payment_method.attached':
                payment_method = event['data']['object']
                logger.info(f"PaymentMethod attached: {payment_method.id}")
            
            elif event_type == 'customer.subscription.updated':
                subscription = event['data']['object']
                await handle_subscription_updated(subscription, user_repo, session)
            
            elif event_type == 'customer.subscription.deleted':
                subscription = event['data']['object']
                await handle_subscription_deleted(subscription, user_repo, session)
            
            # Commit the transaction
            await session.commit()
            
    except Exception as e:
        # Log the error but don't crash - we'll retry on the next webhook delivery
        logger.error(f"Error processing Stripe webhook event {event.get('id')}: {str(e)}", 
                    exc_info=True)
        # Re-raise to trigger retry if configured
        raise

async def handle_subscription_updated(
    subscription: Dict[str, Any],
    user_repo: AsyncUserRepository,
    session: AsyncSession
):
    """
    Handle subscription updated webhook event from Stripe
    
    This function updates the user's subscription status in the database
    when a subscription is updated in Stripe.
    
    Args:
        subscription: The subscription data from Stripe
        user_repo: The user repository instance
        session: The database session
    """
    try:
        stripe_subscription_id = subscription['id']
        
        # Find user with this subscription
        user = await user_repo.get_user_by_stripe_subscription_id(
            stripe_subscription_id,
            session=session
        )
        
        if not user:
            logger.warning(f"User with subscription {stripe_subscription_id} not found")
            return
            
        # Prepare update data
        update_data = {
            'subscription_status': subscription['status'],
            'current_period_end': datetime.fromtimestamp(subscription['current_period_end']),
            'cancel_at_period_end': subscription.get('cancel_at_period_end', False),
            'updated_at': datetime.utcnow()
        }
        
        # If subscription is canceled or incomplete, clear plan details
        if subscription['status'] in ['canceled', 'incomplete_expired']:
            update_data.update({
                'plan_id': None,
                'plan_name': None,
                'trial_ends_at': None
            })
        
        # Update user in database
        await user_repo.update_user(
            user.id,
            update_data,
            session=session
        )
        
        logger.info(
            f"Updated subscription for user {user.id} (subscription: {stripe_subscription_id}) "
            f"to status: {subscription['status']}"
        )
        
    except Exception as e:
        logger.error(
            f"Error handling subscription.updated for {subscription.get('id')}: {str(e)}", 
            exc_info=True
        )
        raise

async def handle_subscription_deleted(
    subscription: Dict[str, Any],
    user_repo: AsyncUserRepository,
    session: AsyncSession
):
    """
    Handle subscription deleted webhook event from Stripe
    
    This function updates the user's subscription status in the database
    when a subscription is deleted in Stripe.
    
    Args:
        subscription: The subscription data from Stripe
        user_repo: The user repository instance
        session: The database session
    """
    try:
        stripe_subscription_id = subscription['id']
        
        # Find user with this subscription
        user = await user_repo.get_user_by_stripe_subscription_id(
            stripe_subscription_id,
            session=session
        )
        
        if not user:
            logger.warning(f"User with subscription {stripe_subscription_id} not found")
            return
            
        # Prepare update data
        update_data = {
            'subscription_status': 'canceled',
            'plan_id': None,
            'plan_name': None,
            'trial_ends_at': None,
            'current_period_end': None,
            'canceled_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        
        # Update user in database
        await user_repo.update_user(
            user.id,
            update_data,
            session=session
        )
        
        logger.info(
            f"Marked subscription as canceled for user {user.id} "
            f"(subscription: {stripe_subscription_id})"
        )
        
    except Exception as e:
        logger.error(
            f"Error handling subscription.deleted for {subscription.get('id')}: {str(e)}", 
            exc_info=True
        )
        raise

# Export the router
subscriptions_router = router

# Import models here to avoid circular imports
from api.models.orm_models import Subscription, CardDetails
