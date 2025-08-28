from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from api.models import User, File, ChatHistory, Subscription
from .async_base_repository import AsyncBaseRepository


class AsyncUserRepository(AsyncBaseRepository[User, Any, Any]):
    def __init__(self, repository_manager):
        from api.models import User
        super().__init__(User, repository_manager)

    async def get_user_by_email(self, email: str):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalars().first()

    async def get_user_by_id(self, user_id: int):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            return result.scalars().first()

    async def user_exists(self, email: str) -> bool:
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalars().first() is not None

    async def get_user_files(self, user_id: int):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(File).where(File.user_id == user_id))
            return result.scalars().all()

    async def get_user_chats(self, user_id: int):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(Chat).where(Chat.user_id == user_id))
            return result.scalars().all()

    async def get_user_subscription(self, user_id: int):
        async with self._repository_manager.session_scope() as session:
            # Query subscription with card details joined
            result = await session.execute(
                select(Subscription)
                .options(selectinload(Subscription.card_details))
                .where(Subscription.user_id == user_id)
            )
            subscription = result.scalars().first()
            
            if not subscription:
                return None
                
            # Format the response to match frontend expectations
            subscription_data = {
                'subscription_status': subscription.status,
                'card_last4': None,
                'card_brand': None,
                'card_exp_month': None,
                'card_exp_year': None
            }
            
            # Check for card details in the relationship
            if hasattr(subscription, 'card_details') and subscription.card_details:
                # The card details are stored in the related CardDetails table
                subscription_data.update({
                    'card_last4': subscription.card_details.card_last4,
                    'card_brand': subscription.card_details.card_type,
                    'card_exp_month': subscription.card_details.exp_month,
                    'card_exp_year': subscription.card_details.exp_year
                })
            
            return subscription_data

    async def search_users(self, query: str):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.email.ilike(f"%{query}%")))
            return result.scalars().all()

    async def update_last_login(self, user_id: int):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalars().first()
            if user:
                from datetime import datetime
                user.last_login = datetime.utcnow()
                await session.commit()
                return user
            return None

    async def add_or_update_subscription(self, user_id: int, subscription_data: dict):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
            subscription = result.scalars().first()
            if subscription:
                for key, value in subscription_data.items():
                    setattr(subscription, key, value)
            else:
                subscription = Subscription(user_id=user_id, **subscription_data)
                session.add(subscription)
            try:
                await session.commit()
                return subscription
            except IntegrityError:
                await session.rollback()
                raise

    async def update_subscription(self, subscription_id: int, update_data: dict):
        async with self._repository_manager.session_scope() as session:
            result = await session.execute(select(Subscription).where(Subscription.id == subscription_id))
            subscription = result.scalars().first()
            if subscription:
                for key, value in update_data.items():
                    setattr(subscription, key, value)
                await session.commit()
            return subscription

    async def get_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get subscription details for a user.
        
        Args:
            user_id: The ID of the user
            
        Returns:
            Optional[Dict]: Subscription details or None if not found
        """
        async with self._repository_manager.session_scope() as session:
            try:
                from ..models.orm_models import User, Subscription, CardDetails
                
                # Check if user exists
                user_result = await session.execute(
                    select(User)
                    .where(User.id == user_id)
                )
                user = user_result.scalars().first()
                
                if not user or not hasattr(user, 'stripe_customer_id') or not user.stripe_customer_id:
                    return {
                        'status': 'none',
                        'stripe_customer_id': None,
                        'current_period_end': None,
                        'cancel_at_period_end': False,
                        'payment_methods': []
                    }
                
                # Get the subscription from the database
                result = await session.execute(
                    select(Subscription)
                    .where(Subscription.stripe_customer_id == user.stripe_customer_id)
                    .options(selectinload(Subscription.card_details))
                    .order_by(Subscription.created_at.desc())
                )
                subscription = result.scalars().first()
                
                if not subscription:
                    return {
                        'status': 'none',
                        'stripe_customer_id': user.stripe_customer_id,
                        'current_period_end': None,
                        'cancel_at_period_end': False,
                        'payment_methods': []
                    }
                    
                # Get card details if available
                card_details = None
                if hasattr(subscription, 'card_details') and subscription.card_details:
                    card = subscription.card_details[0]  # Assuming one card per subscription
                    card_details = {
                        'id': f'pm_{subscription.stripe_customer_id[-8:]}',
                        'card': {
                            'last4': card.card_last4,
                            'brand': card.card_type.lower(),
                            'exp_month': card.exp_month,
                            'exp_year': card.exp_year
                        }
                    }
                
                # Convert subscription to dict and add payment method info
                subscription_data = {
                    'id': subscription.stripe_subscription_id or f'sub_{subscription.id}',
                    'status': subscription.status,
                    'stripe_customer_id': subscription.stripe_customer_id,
                    'current_period_end': int(subscription.current_period_end.timestamp()) 
                    if subscription.current_period_end else None,
                    'cancel_at_period_end': getattr(subscription, 'cancel_at_period_end', False),
                    'payment_methods': [card_details] if card_details else []
                }
                
                return subscription_data
                
            except Exception as e:
                logger.error(f"Error getting subscription: {e}", exc_info=True)
                return {
                    'status': 'error',
                    'stripe_customer_id': None,
                    'current_period_end': None,
                    'cancel_at_period_end': False,
                    'payment_methods': [],
                    'error': str(e)
                }

    async def update_subscription_status(
        self, 
        stripe_customer_id: str, 
        status: str, 
        current_period_end: Optional[datetime] = None,
        cancel_at_period_end: bool = False,
        stripe_subscription_id: Optional[str] = None,
        stripe_payment_method_id: Optional[str] = None,
        card_last4: Optional[str] = None,
        card_brand: Optional[str] = None,
        exp_month: Optional[int] = None,
        exp_year: Optional[int] = None
    ) -> bool:
        """
        Update or create a subscription status in the database.
        
        Args:
            stripe_customer_id: Stripe customer ID
            status: Subscription status (e.g., 'active', 'canceled', 'past_due')
            current_period_end: When the current billing period ends
            cancel_at_period_end: Whether the subscription is set to cancel at period end
            stripe_subscription_id: Optional Stripe subscription ID
            stripe_payment_method_id: Optional Stripe payment method ID
            card_last4: Last 4 digits of the card
            card_brand: Brand of the card (e.g., 'visa', 'mastercard')
            exp_month: Expiration month
            exp_year: Expiration year
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        async with self._repository_manager.session_scope() as session:
            try:
                from ..models.orm_models import Subscription, CardDetails, User
                from sqlalchemy.orm import selectinload
                
                # Check if subscription exists
                result = await session.execute(
                    select(Subscription)
                    .where(Subscription.stripe_customer_id == stripe_customer_id)
                    .options(selectinload(Subscription.card_details))
                )
                subscription = result.scalars().first()
                
                # If no subscription found, try to find by user ID
                if not subscription and stripe_customer_id.startswith('cus_'):
                    user_result = await session.execute(
                        select(User)
                        .where(User.stripe_customer_id == stripe_customer_id)
                    )
                    user = user_result.scalars().first()
                    
                    if user:
                        result = await session.execute(
                            select(Subscription)
                            .where(Subscription.user_id == user.id)
                            .options(selectinload(Subscription.card_details))
                        )
                        subscription = result.scalars().first()
                
                if subscription:
                    # Update existing subscription
                    subscription.status = status
                    subscription.updated_at = datetime.utcnow()
                    
                    if current_period_end:
                        if isinstance(current_period_end, (int, float)):
                            from datetime import datetime
                            current_period_end = datetime.fromtimestamp(current_period_end)
                        subscription.current_period_end = current_period_end
                        
                    if stripe_subscription_id:
                        subscription.stripe_subscription_id = stripe_subscription_id
                        
                    if hasattr(subscription, 'cancel_at_period_end'):
                        subscription.cancel_at_period_end = cancel_at_period_end
                    
                    # Update or create card details if payment method info is provided
                    if any([stripe_payment_method_id, card_last4, card_brand, exp_month, exp_year]):
                        if hasattr(subscription, 'card_details') and subscription.card_details:
                            # Update existing card details
                            card = subscription.card_details[0]
                            if stripe_payment_method_id:
                                card.stripe_payment_method_id = stripe_payment_method_id
                            if card_last4:
                                card.card_last4 = card_last4
                            if card_brand:
                                card.card_type = card_brand
                            if exp_month is not None:
                                card.exp_month = exp_month
                            if exp_year is not None:
                                card.exp_year = exp_year
                        else:
                            # Create new card details
                            card = CardDetails(
                                subscription_id=subscription.id,
                                card_last4=card_last4 or '0000',
                                card_type=card_brand or 'unknown',
                                exp_month=exp_month or 1,
                                exp_year=exp_year or (datetime.utcnow().year + 1)
                            )
                            session.add(card)
                else:
                    # Find user by stripe_customer_id
                    user_result = await session.execute(
                        select(User)
                        .where(User.stripe_customer_id == stripe_customer_id)
                    )
                    user = user_result.scalars().first()
                    
                    if not user:
                        logger.error(f"No user found with stripe_customer_id: {stripe_customer_id}")
                        return False
                    
                    # Create new subscription
                    subscription = Subscription(
                        user_id=user.id,
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        status=status,
                        current_period_end=current_period_end,
                        cancel_at_period_end=cancel_at_period_end,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    session.add(subscription)
                    await session.flush()  # Get the subscription ID
                    
                    # Add card details if payment method info is provided
                    if any([stripe_payment_method_id, card_last4, card_brand, exp_month, exp_year]):
                        card = CardDetails(
                            subscription_id=subscription.id,
                            card_last4=card_last4 or '0000',
                            card_type=card_brand or 'unknown',
                            exp_month=exp_month or 1,
                            exp_year=exp_year or (datetime.utcnow().year + 1)
                        )
                        session.add(card)
                
                return True  # Session will be committed by the context manager
                
            except Exception as e:
                logger.error(f"Error updating subscription status: {e}", exc_info=True)
                raise  # Let the context manager handle rollback

    async def add_user(self, email: str, username: str) -> Optional[int]:
        """
        Add a new user to the database.
        
        Args:
            email: User's email address
            username: User's display name
            
        Returns:
            int: ID of the created user if successful, None otherwise
        """
        try:
            user = User(email=email, username=username)
            async with self._repository_manager.session_scope() as session:
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user.id
        except IntegrityError as e:
            logger.error(f"Error adding user {email}: {str(e)}")
            await session.rollback()
            return None
            
    async def get_user_id_from_email(self, email: str) -> Optional[int]:
        """
        Get a user's ID from their email address.
        
        Args:
            email: Email address to look up
            
        Returns:
            Optional[int]: User ID if found, None otherwise
        """
        user = await self.get_user_by_email(email)
        return user.id if user else None
        
    async def delete_user_account(self, user_id: int) -> bool:
        """
        Delete a user account and all associated data.
        
        Args:
            user_id: ID of the user to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            async with self._repository_manager.session_scope() as session:
                # Get the user with all relationships loaded
                result = await session.execute(
                    select(User)
                    .options(selectinload('*'))
                    .where(User.id == user_id)
                )
                user = result.scalars().first()
                
                if not user:
                    return False
                    
                # Delete all user data
                await session.delete(user)
                await session.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {str(e)}")
            await session.rollback()
            return False
            
    async def is_premium_user(self, user_id: int) -> bool:
        """
        Check if a user has an active premium subscription.
        
        Args:
            user_id: ID of the user to check
            
        Returns:
            bool: True if the user has an active premium subscription, False otherwise
        """
        subscription = await self.get_user_subscription(user_id)
        return subscription is not None and subscription.status == 'active'
