"""
Asynchronous repository for user-related database operations.

This module provides an implementation of AsyncBaseRepository specifically for
User model operations, handling all database interactions related to users.
"""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from typing import (
    Any, Dict, List, Optional, Union, Tuple, 
    AsyncGenerator, Type, TypeVar, cast, overload
)

from sqlalchemy import select, update, delete, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import (
    SQLAlchemyError, IntegrityError, NoResultFound,
    MultipleResultsFound, DBAPIError, InterfaceError
)
from sqlalchemy.orm import joinedload, selectinload, load_only
from sqlalchemy.dialects.postgresql import insert

from ..models.orm_models import (
    User, 
    File, 
    ChatHistory,
    Subscription, 
    CardDetails,
    KeyConcept,
    SubscriptionStatus,
    Base
)
from ..models.user import UserCreate, UserUpdate, UserInDB
from .async_base_repository import AsyncBaseRepository
from ..core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Type aliases
JSONType = Dict[str, Any]
T = TypeVar('T', bound=User)


class AsyncUserRepository(AsyncBaseRepository[User, UserCreate, UserUpdate]):
    """
    Asynchronous repository for user operations.
    
    This class provides methods for all database operations related to users,
    including CRUD operations, authentication, and user management.
    
    Args:
        session_factory: Optional async session factory function. If not provided,
                        will use the default from the base repository.
    """
    
    def __init__(self, repository_manager, session_factory=None):
        """
        Initialize the user repository.
        
        Args:
            repository_manager: The repository manager instance
            session_factory: Optional SQLAlchemy async session factory
        """
        super().__init__(repository_manager, session_factory)
        self._model = User
        self._initialized = True

    # ---------------------- USER METHODS ----------------------
    async def get_by_email(
        self, 
        email: str, 
        include_inactive: bool = False,
        include_relationships: bool = False
    ) -> Optional[User]:
        """
        Get a user by email with optional relationships.
        
        Args:
            email: The email address to search for
            include_inactive: Whether to include inactive users
            include_relationships: Whether to load related entities
            
        Returns:
            Optional[User]: The user if found, None otherwise
            
        Raises:
            SQLAlchemyError: If there's an error executing the query
        """
        async with self.session_scope() as session:
            try:
                query = select(User).where(User.email == email)
                
                if not include_inactive:
                    query = query.where(User.is_active == True)
                
                if include_relationships:
                    query = query.options(
                        selectinload(User.subscription),
                        selectinload(User.card_details),
                        joinedload(User.files).load_only(
                            File.id, File.filename, File.file_type, 
                            File.created_at, File.updated_at
                        ),
                        joinedload(User.chat_histories).load_only(
                            ChatHistory.id, ChatHistory.title, 
                            ChatHistory.created_at, ChatHistory.updated_at
                        )
                    )
                
                result = await session.execute(query)
                user = result.scalar_one_or_none()
                
                if user and include_relationships:
                    # Ensure we've loaded all relationships
                    await session.refresh(user, [
                        'subscription', 'card_details', 
                        'files', 'chat_histories'
                    ])
                
                return user
                
            except SQLAlchemyError as e:
                logger.error("Error getting user by email %s: %s", email, str(e), exc_info=True)
                raise

    async def delete(self, user_id: int, hard_delete: bool = False) -> bool:
        """
        Delete a user with option for hard or soft delete.
        
        Args:
            user_id: The ID of the user to delete
            hard_delete: If True, permanently delete the user. 
                        If False, perform a soft delete.
                        
        Returns:
            bool: True if the user was deleted, False if not found
            
        Raises:
            SQLAlchemyError: If there's an error deleting the user
        """
        async with self.session_scope() as session:
            try:
                # Get the user with a lock
                stmt = (
                    select(User)
                    .where(User.id == user_id)
                    .with_for_update()
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    return False
                
                if hard_delete:
                    # Hard delete - remove the user completely
                    await session.delete(user)
                    logger.info("Hard deleted user with ID: %s", user_id)
                else:
                    # Soft delete - mark as inactive and anonymize
                    user.is_active = False
                    user.email = f"deleted_{user.id}_{user.email}"
                    user.updated_at = datetime.now(timezone.utc)
                    logger.info("Soft deleted user with ID: %s", user_id)
                
                await session.flush()
                return True
                
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Error deleting user %s: %s", user_id, str(e), exc_info=True)
                raise

    async def get_subscription_by_customer_id(self, stripe_customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription by Stripe customer ID.
        
        Args:
            stripe_customer_id: The Stripe customer ID to look up
            
        Returns:
            Optional[Dict]: Subscription data if found, None otherwise
        """
        try:
            async with self.session_scope() as session:
                result = await session.execute(
                    select(Subscription)
                    .where(Subscription.stripe_customer_id == stripe_customer_id)
                    .options(joinedload(Subscription.card_details))
                )
                subscription = result.scalar_one_or_none()
                
                if not subscription:
                    return None
                    
                return self._format_subscription_data(subscription)
                
        except SQLAlchemyError as e:
            logger.error(f"Error getting subscription by customer ID {stripe_customer_id}: {str(e)}")
            return None

    async def update(
        self, 
        user_id: int, 
        user_data: Union[UserUpdate, Dict[str, Any]],
        **kwargs
    ) -> Optional[User]:
        """
        Update a user with additional attributes.
        
        Args:
            user_id: The ID of the user to update
            user_data: The data to update (can be dict or UserUpdate)
            **kwargs: Additional attributes to update
            
        Returns:
            Optional[User]: The updated user if found, None otherwise
            
        Raises:
            SQLAlchemyError: If there's an error updating the user
        """
        async with self.session_scope() as session:
            try:
                # Get the user with a lock to prevent concurrent updates
                stmt = (
                    select(User)
                    .where(User.id == user_id)
                    .with_for_update()
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    return None
                
                # Convert to dict if needed
                if not isinstance(user_data, dict):
                    user_data = user_data.dict(exclude_unset=True)
                
                # Merge with any additional kwargs
                update_data = {**user_data, **kwargs}
                
                # Update fields
                for field, value in update_data.items():
                    if hasattr(user, field) and field != 'id' and value is not None:
                        setattr(user, field, value)
                
                # Update timestamps
                user.updated_at = datetime.now(timezone.utc)
                
                await session.flush()
                await session.refresh(user)
                
                logger.info("Updated user with ID: %s", user_id)
                return user
                
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Error updating user %s: %s", user_id, str(e), exc_info=True)
                raise

    async def create(self, user_data: Union[UserCreate, Dict[str, Any]], **kwargs) -> User:
        """
        Create a new user with optional additional attributes.
        
        Args:
            user_data: User data to create (can be dict or UserCreate)
            **kwargs: Additional attributes to set on the user
            
        Returns:
            User: The created user with ID populated
            
        Raises:
            IntegrityError: If a user with the same email already exists
            SQLAlchemyError: If there's an error creating the user
        """
        async with self.session_scope() as session:
            try:
                if isinstance(user_data, dict):
                    user_data = UserCreate(**user_data)
                
                # Create base user from model
                user_data_dict = user_data.dict(exclude_unset=True)
                user_data_dict.update(kwargs)  # Add any additional attributes
                
                user = User(**user_data_dict)
                
                # Set timestamps
                now = datetime.now(timezone.utc)
                user.created_at = now
                user.updated_at = now
                
                # Add and commit
                session.add(user)
                await session.flush()
                
                # Create default subscription
                subscription = Subscription(
                    user_id=user.id,
                    plan_id=settings.DEFAULT_SUBSCRIPTION_PLAN,
                    status=SubscriptionStatus.ACTIVE,
                    start_date=now,
                    end_date=None,  # No end date for default subscription
                    auto_renew=True
                )
                session.add(subscription)
                
                await session.commit()
                await session.refresh(user)
                
                logger.info("Created new user with ID: %s", user.id)
                return user
                
            except IntegrityError as e:
                await session.rollback()
                logger.error("Integrity error creating user: %s", str(e), exc_info=True)
                raise
                
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Error creating user: %s", str(e), exc_info=True)
                raise

    async def delete_user_account(self, user_id: int) -> bool:
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("Invalid user_id provided")

        async with self.session_scope() as session:
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
        async with get_session() as s:
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
                            'brand': getattr(card, 'card_brand', None),
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
        """
        Get basic subscription info for a user by their internal user ID.
        
        Args:
            user_id: Internal integer user ID
            
        Returns:
            Optional[Dict]: Subscription data if found, None otherwise
        """
        return await self._get_subscription_base(user_id, include_payment_methods=False)

    async def get_subscription_with_card(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get subscription info including payment methods for a user by their internal user ID.
        
        Args:
            user_id: Internal integer user ID
            
        Returns:
            Optional[Dict]: Subscription data with payment methods if found, None otherwise
        """
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
            async with self.get_session() as s:
                session = s
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
                    card.card_brand = card_brand
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
