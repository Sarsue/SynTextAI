"""
Async User repository for managing user-related database operations.

This module mirrors the sync UserRepository but provides async functionality
while maintaining identical method signatures and return types.
"""
from typing import Optional, List, Tuple, Dict
import logging
from sqlalchemy import text, select, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .async_base_repository import AsyncBaseRepository

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
        exp_year=None,
        organization_id: Optional[int] = None,
        seats: Optional[int] = None,
        plan_key: Optional[str] = None,
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
            seats: Seats the plan includes before overage applies. Written here
                so seat limits survive without a Stripe round trip on every
                page load.
            plan_key: Which plan. Seat pricing differs per plan, so the overage
                rate cannot be worked out without it.

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
                    # Repair the link on rows written before subscriptions moved
                    # to organizations.
                    if organization_id and not existing_sub.organization_id:
                        existing_sub.organization_id = organization_id
                    if current_period_end:
                        existing_sub.current_period_end = current_period_end
                    if trial_end:
                        existing_sub.trial_end = trial_end
                    if seats is not None:
                        existing_sub.seats = seats
                    if plan_key is not None:
                        existing_sub.plan_key = plan_key

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
                        # The organization is what pays. Entitlement is read by
                        # organization, so a subscription without one leaves the
                        # tenant looking unpaid and locks the customer out of the
                        # app they just subscribed to.
                        organization_id=organization_id,
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        status=status,
                        current_period_end=current_period_end,
                        trial_end=trial_end,
                        seats=seats,
                        plan_key=plan_key,
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
        exp_year=None,
        seats=None,
        plan_key=None,
        stripe_subscription_id: Optional[str] = None,
    ) -> bool:
        """Update one subscription, identified by subscription id where possible.

        The customer is the wrong key on its own. A Stripe customer can carry
        more than one subscription, which happens as soon as one person owns two
        organizations, and this used scalar_one_or_none: the second row did not
        pick the wrong one, it raised MultipleResultsFound and took the whole
        webhook down with it. The subscription id identifies exactly one row.

        The customer id is still accepted and still used as a fallback, because
        rows written before subscriptions carried an id have nothing else.

        Args:
            stripe_customer_id: Stripe customer ID
            status: New subscription status
            current_period_end: End of current subscription period
            card_last4: Last 4 digits of payment card
            card_type: Type of payment card
            exp_month: Card expiration month
            exp_year: Card expiration year
            stripe_subscription_id: Stripe subscription ID, preferred key

        Returns:
            bool: True if successful, False otherwise
        """
        async with self.get_async_session() as session:
            try:
                subscription = None
                if stripe_subscription_id:
                    subscription = (await session.execute(
                        select(SubscriptionORM).where(
                            SubscriptionORM.stripe_subscription_id == stripe_subscription_id
                        )
                    )).scalars().first()

                if subscription is None:
                    # Fall back to the customer. first(), not
                    # scalar_one_or_none(): if two rows really do share a
                    # customer, updating the older one is wrong but recoverable,
                    # while raising loses the event entirely and Stripe retries
                    # it forever.
                    rows = (await session.execute(
                        select(SubscriptionORM)
                        .where(SubscriptionORM.stripe_customer_id == stripe_customer_id)
                        .order_by(SubscriptionORM.id.desc())
                    )).scalars().all()
                    if len(rows) > 1:
                        logger.warning(
                            "Stripe customer %s maps to %s subscription rows; updating the newest. "
                            "A customer should belong to one organization.",
                            stripe_customer_id, len(rows),
                        )
                    subscription = rows[0] if rows else None

                if not subscription:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False

                # Update subscription
                subscription.status = status
                if current_period_end:
                    subscription.current_period_end = current_period_end
                # Written from the webhook so a plan changed in the Stripe
                # dashboard reaches the seat accounting here.
                if seats is not None:
                    subscription.seats = seats
                if plan_key is not None:
                    subscription.plan_key = plan_key

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

    async def get_subscription(self, user_id: int) -> Optional[Tuple[Dict[str, any], Optional[Dict[str, any]]]]:
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

                # Convert to dict
                subscription = {
                    'id': sub_orm.id,
                    'user_id': sub_orm.user_id,
                    'stripe_customer_id': sub_orm.stripe_customer_id,
                    'stripe_subscription_id': sub_orm.stripe_subscription_id,
                    'status': sub_orm.status,
                    'current_period_end': sub_orm.current_period_end,
                    'trial_end': sub_orm.trial_end,
                    'created_at': sub_orm.created_at,
                    'updated_at': sub_orm.updated_at,
                }

                # Get card details if they exist
                stmt = select(CardDetailsORM).where(CardDetailsORM.subscription_id == sub_orm.id)
                result = await session.execute(stmt)
                card_details_orm = result.scalar_one_or_none()

                card_details = None
                if card_details_orm:
                    card_details = {
                        'id': card_details_orm.id,
                        'subscription_id': card_details_orm.subscription_id,
                        'card_last4': card_details_orm.card_last4,
                        'card_type': card_details_orm.card_type,
                        'exp_month': card_details_orm.exp_month,
                        'exp_year': card_details_orm.exp_year,
                        'created_at': card_details_orm.created_at,
                    }

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

