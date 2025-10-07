"""
Async User repository for managing user-related database operations.

This module mirrors the sync UserRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Tuple
import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .async_base_repository import AsyncBaseRepository
from .domain_models import User, Subscription, CardDetails

# Import ORM models from the new models module
from ..models import User as UserORM
from ..models import Subscription as SubscriptionORM
from ..models import CardDetails as CardDetailsORM

logger = logging.getLogger(__name__)


class AsyncUserRepository(AsyncBaseRepository):
    """Async repository for user-related database operations."""

    async def add_user(self, email: str, username: str) -> Optional[int]:
        """Add a new user to the database.

        Args:
            email: User's email address
            username: User's username

        Returns:
            int: The ID of the newly created user, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                new_user = UserORM(email=email, username=username)
                session.add(new_user)
                await session.flush()  # Flush to get the ID without committing
                await session.refresh(new_user)
                return new_user.id
            except IntegrityError:
                await session.rollback()
                logger.error(f"User with email '{email}' or username '{username}' already exists.")
                return None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding user: {e}", exc_info=True)
                return None

    async def get_user_id_from_email(self, email: str) -> Optional[int]:
        """Get user ID from email.

        Args:
            email: User's email address

        Returns:
            int: The user ID if found, None otherwise
        """
        async with self.get_async_session() as session:
            user = await session.get(UserORM, email)  # This should work for email lookup
            return user.id if user else None

    async def delete_user_account(self, user_id: int) -> bool:
        """Delete a user account and all associated data.

        Args:
            user_id: ID of the user to delete

        Returns:
            bool: True if the deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if the user exists
                user = await session.get(UserORM, user_id)
                if not user:
                    logger.warning(f"Attempted to delete non-existent user: {user_id}")
                    return False

                # The cascade should handle deleting related objects
                await session.delete(user)
                await session.commit()
                logger.info(f"Successfully deleted user {user_id} with cascade")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)

                # Fallback to direct SQL for cleanup if ORM cascade fails
                try:
                    await session.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
                    await session.commit()
                    logger.info(f"Deleted user {user_id} using direct SQL after ORM failure")
                    return True
                except Exception as sql_error:
                    await session.rollback()
                    logger.error(f"SQL fallback error deleting user {user_id}: {sql_error}", exc_info=True)
                    return False

    async def add_or_update_subscription(
        self,
        user_id: int,
        stripe_customer_id: str,
        stripe_subscription_id: Optional[str],
        status: str,
        current_period_start: Optional[int],
        current_period_end: Optional[int],
        cancel_at_period_end: bool,
        plan_type: str
    ) -> bool:
        """Add or update a user subscription.

        Args:
            user_id: ID of the user
            stripe_customer_id: Stripe customer ID
            stripe_subscription_id: Stripe subscription ID (optional)
            status: Subscription status
            current_period_start: Start of current period (Unix timestamp)
            current_period_end: End of current period (Unix timestamp)
            cancel_at_period_end: Whether to cancel at period end
            plan_type: Type of subscription plan

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if subscription already exists
                existing_subscription = await session.query(SubscriptionORM).filter(
                    SubscriptionORM.stripe_customer_id == stripe_customer_id
                ).first()

                if existing_subscription:
                    # Update existing subscription
                    existing_subscription.status = status
                    existing_subscription.stripe_subscription_id = stripe_subscription_id
                    existing_subscription.current_period_start = current_period_start
                    existing_subscription.current_period_end = current_period_end
                    existing_subscription.cancel_at_period_end = cancel_at_period_end
                    existing_subscription.plan_type = plan_type
                else:
                    # Create new subscription
                    new_subscription = SubscriptionORM(
                        user_id=user_id,
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        status=status,
                        current_period_start=current_period_start,
                        current_period_end=current_period_end,
                        cancel_at_period_end=cancel_at_period_end,
                        plan_type=plan_type
                    )
                    session.add(new_subscription)

                await session.commit()
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding/updating subscription for user {user_id}: {e}", exc_info=True)
                return False

    async def update_subscription(
        self,
        stripe_customer_id: str,
        status: str,
        current_period_end: Optional[int] = None
    ) -> bool:
        """Update a subscription by Stripe customer ID.

        Args:
            stripe_customer_id: Stripe customer ID
            status: New subscription status
            current_period_end: New period end timestamp (optional)

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                subscription = await session.query(SubscriptionORM).filter(
                    SubscriptionORM.stripe_customer_id == stripe_customer_id
                ).first()

                if not subscription:
                    logger.error(f"No subscription found for customer {stripe_customer_id}")
                    return False

                subscription.status = status
                if current_period_end is not None:
                    subscription.current_period_end = current_period_end

                await session.commit()
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating subscription for customer {stripe_customer_id}: {e}", exc_info=True)
                return False

    async def get_subscription(self, user_id: int) -> Optional[Tuple[Subscription, Optional[CardDetails]]]:
        """Get subscription details for a user.

        Args:
            user_id: ID of the user

        Returns:
            Optional[Tuple[Subscription, Optional[CardDetails]]]: Subscription and card details if found
        """
        async with self.get_async_session() as session:
            try:
                subscription = await session.query(SubscriptionORM).filter(
                    SubscriptionORM.user_id == user_id
                ).first()

                if not subscription:
                    return None

                # Get card details if they exist
                card_details = None
                if subscription.stripe_customer_id:
                    card_details = await session.query(CardDetailsORM).filter(
                        CardDetailsORM.stripe_customer_id == subscription.stripe_customer_id
                    ).first()

                return (subscription, card_details)

            except Exception as e:
                logger.error(f"Error getting subscription for user {user_id}: {e}", exc_info=True)
                return None

    async def update_subscription_status(self, stripe_customer_id: str, new_status: str) -> bool:
        """Update subscription status by Stripe customer ID (for webhooks).

        Args:
            stripe_customer_id: Stripe customer ID
            new_status: New subscription status

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                subscription = await session.query(SubscriptionORM).filter(
                    SubscriptionORM.stripe_customer_id == stripe_customer_id
                ).first()

                if not subscription:
                    logger.error(f"No subscription found for customer {stripe_customer_id}")
                    return False

                subscription.status = new_status
                await session.commit()
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating subscription status for customer {stripe_customer_id}: {e}", exc_info=True)
                return False

    async def is_premium_user(self, user_id: int) -> bool:
        """Check if a user has an active premium subscription.

        Args:
            user_id: ID of the user

        Returns:
            bool: True if user has active premium subscription
        """
        async with self.get_async_session() as session:
            try:
                subscription = await session.query(SubscriptionORM).filter(
                    SubscriptionORM.user_id == user_id,
                    SubscriptionORM.status == 'active'
                ).first()

                return subscription is not None

            except Exception as e:
                logger.error(f"Error checking premium status for user {user_id}: {e}", exc_info=True)
                return False
