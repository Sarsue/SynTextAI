"""
Async User repository for managing user-related database operations.

This module mirrors the sync UserRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Tuple
import logging
from sqlalchemy import text, select, and_, or_
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
    """Async repository for user operations."""

    async def add_user(self, email: str, username: str) -> Optional[int]:
        """Add a new user to the database.

        Args:
            email: User's email address
            username: User's username

        Returns:
            Optional[int]: The ID of the newly created user, or None if creation failed
        """
        async with self.get_async_session() as session:
            try:
                user_orm = UserORM(email=email, username=username)
                session.add(user_orm)
                await session.flush()
                user_id = user_orm.id
                await session.commit()
                logger.info(f"Successfully added user {username} with email {email}")
                return user_id
            except IntegrityError:
                await session.rollback()
                logger.error(f"User with email '{email}' or username '{username}' already exists.")
                return None
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding user {username}: {e}", exc_info=True)
                return None

    async def get_user_id_from_email(self, email: str) -> Optional[int]:
        """Get user ID from email address.

        Args:
            email: User's email address

        Returns:
            Optional[int]: User ID if found, None otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(UserORM).where(UserORM.email == email)
                result = await session.execute(stmt)
                user_orm = result.scalar_one_or_none()
                return user_orm.id if user_orm else None
            except Exception as e:
                logger.error(f"Error getting user ID for email {email}: {e}", exc_info=True)
                return None

    async def delete_user_account(self, user_id: int) -> bool:
        """Delete a user account and all associated data.

        Args:
            user_id: ID of the user to delete

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(UserORM).where(UserORM.id == user_id)
                result = await session.execute(stmt)
                user_orm = result.scalar_one_or_none()

                if not user_orm:
                    logger.warning(f"Attempted to delete non-existent user: {user_id}")
                    return False

                await session.delete(user_orm)
                await session.commit()
                logger.info(f"Successfully deleted user {user_id} with cascade")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
                async with self.get_async_session() as fallback_session:
                    try:
                        await fallback_session.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
                        await fallback_session.commit()
                        logger.info(f"Deleted user {user_id} using direct SQL after ORM failure")
                        return True
                    except Exception as sql_error:
                        await fallback_session.rollback()
                        logger.error(f"SQL fallback error deleting user {user_id}: {sql_error}", exc_info=True)
                        return False

    async def add_or_update_subscription(
        self,
        user_id: int,
        stripe_customer_id: str,
        stripe_subscription_id: Optional[str],
        status: str,
        current_period_end=None,
        trial_end=None,
        card_last4=None,
        card_type=None,
        exp_month=None,
        exp_year=None
    ) -> bool:
        """Add or update a user subscription.

        Args:
            user_id: ID of the user
            stripe_customer_id: Stripe customer ID
            stripe_subscription_id: Stripe subscription ID
            status: Subscription status
            current_period_end: End of current subscription period
            trial_end: End of trial period, if any
            card_last4: Last 4 digits of payment card
            card_type: Type of payment card
            exp_month: Card expiration month
            exp_year: Card expiration year

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Check if user exists
                stmt = select(UserORM).where(UserORM.id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    logger.error(f"No user found with ID: {user_id}")
                    return False

                # Check if subscription already exists
                stmt = select(SubscriptionORM).where(SubscriptionORM.user_id == user_id)
                result = await session.execute(stmt)
                existing_sub = result.scalar_one_or_none()

                if existing_sub:
                    # Update existing subscription
                    existing_sub.stripe_customer_id = stripe_customer_id
                    existing_sub.stripe_subscription_id = stripe_subscription_id
                    existing_sub.status = status
                    if current_period_end:
                        existing_sub.current_period_end = current_period_end
                    if trial_end:
                        existing_sub.trial_end = trial_end

                    # Update card details if provided
                    if all([card_last4, card_type, exp_month, exp_year]):
                        stmt = select(CardDetailsORM).where(CardDetailsORM.subscription_id == existing_sub.id)
                        result = await session.execute(stmt)
                        card_details = result.scalar_one_or_none()

                        if card_details:
                            # Update existing card details
                            card_details.card_last4 = card_last4
                            card_details.card_type = card_type
                            card_details.exp_month = exp_month
                            card_details.exp_year = exp_year
                        else:
                            # Create new card details
                            new_card = CardDetailsORM(
                                subscription_id=existing_sub.id,
                                card_last4=card_last4,
                                card_type=card_type,
                                exp_month=exp_month,
                                exp_year=exp_year
                            )
                            session.add(new_card)

                    await session.commit()
                    logger.info(f"Updated subscription for user {user_id}")
                    return True
                else:
                    # Create new subscription
                    new_sub = SubscriptionORM(
                        user_id=user_id,
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        status=status,
                        current_period_end=current_period_end,
                        trial_end=trial_end
                    )
                    session.add(new_sub)
                    await session.flush()  # To get the ID of the new subscription

                    # Add card details if provided
                    if all([card_last4, card_type, exp_month, exp_year]):
                        new_card = CardDetailsORM(
                            subscription_id=new_sub.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year
                        )
                        session.add(new_card)

                    await session.commit()
                    logger.info(f"Created new subscription for user {user_id}")
                    return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding/updating subscription: {e}", exc_info=True)
                return False

    async def update_subscription(
        self,
        stripe_customer_id: str,
        status: str,
        current_period_end=None,
        card_last4=None,
        card_type=None,
        exp_month=None,
        exp_year=None
    ) -> bool:
        """Update a subscription by Stripe customer ID.

        Args:
            stripe_customer_id: Stripe customer ID
            status: New subscription status
            current_period_end: End of current subscription period
            card_last4: Last 4 digits of payment card
            card_type: Type of payment card
            exp_month: Card expiration month
            exp_year: Card expiration year

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                # Find subscription by stripe_customer_id
                stmt = select(SubscriptionORM).where(SubscriptionORM.stripe_customer_id == stripe_customer_id)
                result = await session.execute(stmt)
                subscription = result.scalar_one_or_none()

                if not subscription:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False

                # Update subscription
                subscription.status = status
                if current_period_end:
                    subscription.current_period_end = current_period_end

                # Update card details if provided
                if all([card_last4, card_type, exp_month, exp_year]):
                    stmt = select(CardDetailsORM).where(CardDetailsORM.subscription_id == subscription.id)
                    result = await session.execute(stmt)
                    card_details = result.scalar_one_or_none()

                    if card_details:
                        # Update existing card details
                        card_details.card_last4 = card_last4
                        card_details.card_type = card_type
                        card_details.exp_month = exp_month
                        card_details.exp_year = exp_year
                    else:
                        # Create new card details
                        new_card = CardDetailsORM(
                            subscription_id=subscription.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year
                        )
                        session.add(new_card)

                await session.commit()
                logger.info(f"Updated subscription for customer {stripe_customer_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating subscription: {e}", exc_info=True)
                return False

    async def get_subscription(self, user_id: int) -> Optional[Tuple[Subscription, Optional[CardDetails]]]:
        """Get user's subscription details.

        Args:
            user_id: ID of the user

        Returns:
            Optional[Tuple[Subscription, Optional[CardDetails]]]: Subscription and card details, or None if not found
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SubscriptionORM).where(SubscriptionORM.user_id == user_id)
                result = await session.execute(stmt)
                sub_orm = result.scalar_one_or_none()

                if not sub_orm:
                    return None

                # Convert to domain model
                subscription = Subscription(
                    id=sub_orm.id,
                    user_id=sub_orm.user_id,
                    stripe_customer_id=sub_orm.stripe_customer_id,
                    stripe_subscription_id=sub_orm.stripe_subscription_id,
                    status=sub_orm.status,
                    current_period_end=sub_orm.current_period_end,
                    trial_end=sub_orm.trial_end,
                    created_at=sub_orm.created_at,
                    updated_at=sub_orm.updated_at
                )

                # Get card details if they exist
                stmt = select(CardDetailsORM).where(CardDetailsORM.subscription_id == sub_orm.id)
                result = await session.execute(stmt)
                card_details_orm = result.scalar_one_or_none()

                card_details = None
                if card_details_orm:
                    card_details = CardDetails(
                        id=card_details_orm.id,
                        subscription_id=card_details_orm.subscription_id,
                        card_last4=card_details_orm.card_last4,
                        card_type=card_details_orm.card_type,
                        exp_month=card_details_orm.exp_month,
                        exp_year=card_details_orm.exp_year,
                        created_at=card_details_orm.created_at
                    )

                return (subscription, card_details)

            except Exception as e:
                logger.error(f"Error getting subscription for user {user_id}: {e}", exc_info=True)
                return None

    async def update_subscription_status(self, stripe_customer_id: str, new_status: str) -> bool:
        """Update subscription status by Stripe customer ID.

        Args:
            stripe_customer_id: Stripe customer ID
            new_status: New subscription status

        Returns:
            bool: True if update was successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SubscriptionORM).where(SubscriptionORM.stripe_customer_id == stripe_customer_id)
                result = await session.execute(stmt)
                subscription = result.scalar_one_or_none()

                if not subscription:
                    logger.error(f"Subscription for customer {stripe_customer_id} not found")
                    return False

                subscription.status = new_status
                await session.commit()
                logger.info(f"Successfully updated subscription status for customer {stripe_customer_id}")
                return True

            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating subscription status for customer {stripe_customer_id}: {e}", exc_info=True)
                return False

    async def is_premium_user(self, user_id: int) -> bool:
        """Check if user has an active premium subscription.

        Args:
            user_id: ID of the user

        Returns:
            bool: True if user has active premium subscription, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(SubscriptionORM).where(
                    and_(
                        SubscriptionORM.user_id == user_id,
                        SubscriptionORM.status.in_(["active", "trialing"])
                    )
                )
                result = await session.execute(stmt)
                subscription = result.scalar_one_or_none()
                return subscription is not None
            except Exception as e:
                logger.error(f"Error checking premium status for user {user_id}: {e}", exc_info=True)
                return False