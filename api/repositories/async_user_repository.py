import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union, Tuple

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.dialects.postgresql import insert

from ..models.orm_models import (
    User, 
    File, 
    ChatHistory,
    Subscription, 
    CardDetails,
    KeyConcept,
    SubscriptionStatus
)
from .async_base_repository import AsyncBaseRepository

logger = logging.getLogger(__name__)

JSONType = Dict[str, Any]


class AsyncUserRepository(AsyncBaseRepository[User, Any, Any]):
    def __init__(self, repository_manager: Any) -> None:
        super().__init__(User, repository_manager)

    # ---------------------- USER METHODS ----------------------
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email address."""
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        async with self._repository_manager.session_scope() as session:
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            return result.scalars().first()
            
    async def get_subscription_by_customer_id(self, stripe_customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription by Stripe customer ID.
        
        Args:
            stripe_customer_id: The Stripe customer ID to look up
            
        Returns:
            Optional[Dict]: Subscription data if found, None otherwise
        """
        async with self._repository_manager.session_scope() as session:
            # Get the subscription with card details
            stmt = (
                select(Subscription, CardDetails)
                .outerjoin(CardDetails, Subscription.id == CardDetails.subscription_id)
                .where(Subscription.stripe_customer_id == stripe_customer_id)
            )
            
            result = await session.execute(stmt)
            row = result.first()
            
            if not row or not row[0]:
                return None
                
            subscription, card_details = row
            
            # Format the subscription data
            subscription_data = {
                'id': subscription.id,
                'user_id': subscription.user_id,
                'status': subscription.status,
                'stripe_customer_id': subscription.stripe_customer_id,
                'stripe_subscription_id': subscription.stripe_subscription_id,
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                'created_at': subscription.created_at.isoformat(),
                'updated_at': subscription.updated_at.isoformat(),
                'trial_end': subscription.trial_end.isoformat() if subscription.trial_end else None,
                'card_details': None
            }
            
            # Add card details if available
            if card_details:
                subscription_data['card_details'] = {
                    'id': card_details.id,
                    'last4': card_details.card_last4,
                    'brand': card_details.card_brand,
                    'exp_month': card_details.exp_month,
                    'exp_year': card_details.exp_year,
                    'stripe_payment_method_id': card_details.stripe_payment_method_id
                }
                
            return subscription_data

    async def add_user(self, email: str, name: str) -> int:
        user = User(email=email, username=name)
        async with self._repository_manager.session_scope() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id

    async def delete_user_account(self, user_id: int) -> bool:
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("Invalid user_id provided")

        async with self._repository_manager.session_scope() as session:
            try:
                user = await self.get_user_by_id(user_id)
                if not user:
                    logger.warning(f"User {user_id} not found")
                    return False

                logger.info(f"Deleting all data for user {user_id}")

                # Soft delete files
                await session.execute(
                    update(File)
                    .where(File.user_id == user_id)
                    .values(is_deleted=True, deleted_at=datetime.utcnow(), updated_at=datetime.utcnow())
                )

                # Delete chat history
                await session.execute(delete(ChatHistory).where(ChatHistory.user_id == user_id))

                # Delete subscription & card
                subscription = await self._get_subscription_by_criteria(user_id=user_id, session=session)
                if subscription:
                    await session.execute(delete(CardDetails).where(CardDetails.subscription_id == subscription.id))
                    await session.delete(subscription)

                # Delete key concepts
                await session.execute(
                    delete(KeyConcept)
                    .join(File, KeyConcept.file_id == File.id)
                    .where(File.user_id == user_id)
                )

                # Delete user-file associations
                await session.execute(delete(UserFile).where(UserFile.user_id == user_id))

                # Delete user
                await session.delete(user)

                await session.commit()
                logger.info(f"Successfully deleted user {user_id} and all related data")
                return True

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Database error deleting user {user_id}: {e}", exc_info=True)
                return False
            except Exception as e:
                await session.rollback()
                logger.error(f"Unexpected error deleting user {user_id}: {e}", exc_info=True)
                return False

    async def is_premium_user(self, user_id: int) -> bool:
        subscription = await self._get_subscription_base(user_id, include_payment_methods=False)
        if not subscription or subscription.get('status') != 'active':
            return False
        current_period_end = subscription.get('current_period_end')
        if current_period_end:
            from dateutil.parser import isoparse
            try:
                return isoparse(current_period_end) > datetime.now(timezone.utc)
            except Exception:
                return False
        return True

    # ---------------------- SUBSCRIPTION METHODS ----------------------
    async def _get_subscription_by_criteria(
        self,
        *,
        user_id: Optional[int] = None,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        session: AsyncSession
    ) -> Optional[Subscription]:
        conditions = []
        if user_id:
            conditions.append(Subscription.user_id == user_id)
        if stripe_customer_id:
            conditions.append(Subscription.stripe_customer_id == stripe_customer_id)
        if stripe_subscription_id:
            conditions.append(Subscription.stripe_subscription_id == stripe_subscription_id)
        if not conditions:
            return None
        stmt = select(Subscription).options(joinedload(Subscription.card_details)).where(*conditions)
        result = await session.execute(stmt)
        return result.scalars().first()

    async def _get_subscription_base(
        self,
        user_id: int,
        include_payment_methods: bool = False,
        session: Optional[AsyncSession] = None
    ) -> Optional[Dict[str, Any]]:
        # If a session is provided, use it directly
        if session is not None:
            subscription = await self._get_subscription_by_criteria(user_id=user_id, session=session)
            if not subscription:
                return None
            data = {
                'id': subscription.id,
                'user_id': subscription.user_id,
                'status': subscription.status.value if hasattr(subscription.status, 'value') else subscription.status,
                'stripe_customer_id': subscription.stripe_customer_id,
                'stripe_subscription_id': subscription.stripe_subscription_id,
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                'created_at': subscription.created_at.isoformat(),
                'updated_at': subscription.updated_at.isoformat(),
                'trial_end': subscription.trial_end.isoformat() if subscription.trial_end else None
            }
            return data
            
        # Otherwise, create a new session
        async with self._repository_manager.session_scope() as s:
            subscription = await self._get_subscription_by_criteria(user_id=user_id, session=s)
            if not subscription:
                return None
            data = {
                'id': subscription.id,
                'user_id': subscription.user_id,
                'status': subscription.status.value if hasattr(subscription.status, 'value') else subscription.status,
                'stripe_customer_id': subscription.stripe_customer_id,
                'stripe_subscription_id': subscription.stripe_subscription_id,
                'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None,
                'created_at': subscription.created_at.isoformat(),
                'updated_at': subscription.updated_at.isoformat(),
                'trial_end': subscription.trial_end.isoformat() if subscription.trial_end else None
            }
            if include_payment_methods:
                data['payment_methods'] = []
                card = subscription.card_details
                if card:
                    # Build the payment method with only the fields we know exist
                    payment_method = {
                        'id': getattr(card, 'id', None),
                        'type': 'card',
                        'card': {
                            'brand': getattr(card, 'card_type', None),
                            'last4': getattr(card, 'card_last4', None),
                            'exp_month': getattr(card, 'exp_month', None),
                            'exp_year': getattr(card, 'exp_year', None)
                        },
                        'subscription_id': getattr(card, 'subscription_id', None)
                    }
                    
                    # Add timestamp fields if they exist
                    if hasattr(card, 'created_at') and card.created_at:
                        payment_method['created_at'] = card.created_at.isoformat()
                    if hasattr(card, 'updated_at') and card.updated_at:
                        payment_method['updated_at'] = card.updated_at.isoformat()
                    
                    # Add stripe_payment_method_id if it exists
                    if hasattr(card, 'stripe_payment_method_id') and card.stripe_payment_method_id:
                        payment_method['stripe_payment_method_id'] = card.stripe_payment_method_id
                    
                    # Filter out None values
                    payment_method = {k: v for k, v in payment_method.items() if v is not None}
                    payment_method['card'] = {k: v for k, v in payment_method.get('card', {}).items() if v is not None}
                    
                    data['payment_methods'].append(payment_method)
            return data

    # ---------------------- SUBSCRIPTION METHODS ----------------------
    async def get_user_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self._get_subscription_base(user_id, include_payment_methods=False)

    async def get_subscription_with_card(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self._get_subscription_base(user_id, include_payment_methods=True)

    async def upsert_subscription(
        self,
        user_id: Optional[int] = None,
        status: Optional[Union[str, SubscriptionStatus]] = None,
        *,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        current_period_end: Optional[Union[str, datetime]] = None,
        trial_end: Optional[Union[str, datetime]] = None,
        card_last4: Optional[str] = None,
        card_brand: Optional[str] = None,
        exp_month: Optional[int] = None,
        exp_year: Optional[int] = None,
        stripe_payment_method_id: Optional[str] = None,
        session: Optional[AsyncSession] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create or update a subscription with optional card details.
        
        Args:
            user_id: The user ID (optional if stripe_customer_id is provided)
            status: The subscription status (e.g., 'active', 'trialing', 'past_due')
            stripe_customer_id: The Stripe customer ID (required if user_id not provided)
            stripe_subscription_id: The Stripe subscription ID
            current_period_end: When the current billing period ends
            trial_end: When the trial period ends (if applicable)
            card_last4: Last 4 digits of the card
            card_brand: Card brand (e.g., 'visa', 'mastercard')
            exp_month: Card expiration month
            exp_year: Card expiration year
            stripe_payment_method_id: Stripe payment method ID
            session: Optional existing database session
            
        Returns:
            Dict containing the subscription details or None if operation failed
            
        Raises:
            ValueError: If required parameters are missing or invalid
        """
        # Validate inputs
        if not user_id and not stripe_customer_id:
            raise ValueError("Either user_id or stripe_customer_id must be provided")
            
        # Convert string status to SubscriptionStatus enum if needed
        if status is not None and isinstance(status, str):
            try:
                status = SubscriptionStatus(status.lower())
            except ValueError as e:
                raise ValueError(f"Invalid status value: {status}") from e
                
        # Convert string dates to datetime objects
        if isinstance(current_period_end, str):
            current_period_end = datetime.fromisoformat(current_period_end.replace("Z", "+00:00"))
        if isinstance(trial_end, str):
            trial_end = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))

        # Validate card info if any card details are provided
        card_info_provided = any([card_last4, card_brand, exp_month, exp_year, stripe_payment_method_id])
        if card_info_provided and not all([card_last4, card_brand, exp_month, exp_year]):
            raise ValueError("All card fields (last4, brand, exp_month, exp_year) must be provided if any are given")
            
        # Use existing session or create a new one
        if session is None:
            session_ctx = self._repository_manager.session_scope()
            session = await session_ctx.__aenter__()
            close_session = True
        else:
            close_session = False

        # Try to find existing subscription
        subscription = await self._get_subscription_by_criteria(
            user_id=user_id,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            session=session
        )
        
        now = datetime.now(timezone.utc)

        if subscription:
            # Update existing subscription
            if status is not None:
                subscription.status = status
            if stripe_customer_id:
                subscription.stripe_customer_id = stripe_customer_id
            if stripe_subscription_id:
                subscription.stripe_subscription_id = stripe_subscription_id
            if current_period_end is not None:
                subscription.current_period_end = current_period_end
            if trial_end is not None:
                subscription.trial_end = trial_end
            subscription.updated_at = now
            session.add(subscription)
        else:
            # Create new subscription
            if status is None:
                status = SubscriptionStatus.ACTIVE  # Default status
                
            subscription = Subscription(
                user_id=user_id,
                status=status,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                current_period_end=current_period_end,
                trial_end=trial_end,
                created_at=now,
                updated_at=now
            )
            session.add(subscription)
            await session.flush()

        try:
            if card_info_provided:
                # Get or create card details
                card = subscription.card_details
                if card:
                    # Update existing card
                    card.card_last4 = card_last4
                    card.card_brand = card_brand  # Fixed field name from card_type to card_brand
                    card.exp_month = exp_month
                    card.exp_year = exp_year
                    if stripe_payment_method_id:
                        card.stripe_payment_method_id = stripe_payment_method_id
                    card.updated_at = now
                    session.add(card)
                else:
                    # Create new card details
                    card = CardDetails(
                        subscription_id=subscription.id,
                        card_last4=card_last4,
                        card_brand=card_brand,
                        exp_month=exp_month,
                        exp_year=exp_year,
                        stripe_payment_method_id=stripe_payment_method_id,
                        created_at=now,
                        updated_at=now
                    )
                    session.add(card)
            
            if close_session:
                await session.commit()
                
            # Return the updated subscription
            result = await self._get_subscription_base(
                subscription.user_id if user_id is None else user_id,
                include_payment_methods=card_info_provided,
                session=session if not close_session else None
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error in upsert_subscription: {str(e)}", exc_info=True)
            if close_session:
                await session.rollback()
            raise
            
        finally:
            if close_session:
                await session_ctx.__aexit__(None, None, None)
