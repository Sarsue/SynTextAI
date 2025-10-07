"""
Asynchronous repository for user-related database operations.

Provides async versions of all user-related DB operations, returning
proper domain models and handling errors consistently.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple, Any

from sqlalchemy import select, delete, text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from ..models.orm_models import User as UserORM, Subscription as SubscriptionORM, CardDetails as CardDetailsORM

from .async_base_repository import AsyncBaseRepository
from ..models.db_utils import get_async_session
from .domain_models import User

logger = logging.getLogger(__name__)


class AsyncUserRepository(AsyncBaseRepository[UserORM, Any, Any]):
    """Asynchronous repository for user operations."""

    async def add_user(self, email: str, username: str) -> Optional[int]:
        """Add a new user to the database and return its ID."""
        async with get_async_session() as session:
            try:
                new_user = UserORM(email=email, username=username)
                session.add(new_user)
                await session.commit()
                await session.refresh(new_user)
                return new_user.id
            except IntegrityError:
                await session.rollback()
                logger.error(f"User with email '{email}' or username '{username}' already exists.")
                return None
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error adding user: {e}", exc_info=True)
                return None

    async def get_user_id_from_email(self, email: str) -> Optional[int]:
        """Get user ID from email."""
        async with get_async_session() as session:
            result = await session.execute(select(UserORM.id).where(UserORM.email == email))
            user_id = result.scalar_one_or_none()
            return user_id

    async def delete_user_account(self, user_id: int) -> bool:
        """Delete a user account and all associated data."""
        async with get_async_session() as session:
            try:
                result = await session.execute(select(UserORM).where(UserORM.id == user_id))
                user = result.scalar_one_or_none()
                if not user:
                    logger.warning(f"Attempted to delete non-existent user: {user_id}")
                    return False
                await session.delete(user)
                await session.commit()
                logger.info(f"Successfully deleted user {user_id} with cascade")
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
                # fallback
                try:
                    await session.execute(text(f"DELETE FROM users WHERE id = :id"), {"id": user_id})
                    await session.commit()
                    logger.info(f"Deleted user {user_id} using direct SQL after ORM failure")
                    return True
                except SQLAlchemyError as sql_error:
                    await session.rollback()
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
        """Add or update a user subscription and card details."""
        async with get_async_session() as session:
            try:
                user = (await session.execute(select(UserORM).where(UserORM.id == user_id))).scalar_one_or_none()
                if not user:
                    logger.error(f"No user found with ID: {user_id}")
                    return False

                sub = (await session.execute(
                    select(SubscriptionORM).where(SubscriptionORM.user_id == user_id)
                )).scalar_one_or_none()

                if sub:
                    # Update subscription
                    sub.stripe_customer_id = stripe_customer_id
                    sub.stripe_subscription_id = stripe_subscription_id
                    sub.status = status
                    if current_period_end:
                        sub.current_period_end = current_period_end
                    if trial_end:
                        sub.trial_end = trial_end

                    # Update card
                    if all([card_last4, card_type, exp_month, exp_year]):
                        card = (await session.execute(
                            select(CardDetailsORM).where(CardDetailsORM.subscription_id == sub.id)
                        )).scalar_one_or_none()
                        if card:
                            card.card_last4 = card_last4
                            card.card_type = card_type
                            card.exp_month = exp_month
                            card.exp_year = exp_year
                        else:
                            new_card = CardDetailsORM(
                                subscription_id=sub.id,
                                card_last4=card_last4,
                                card_type=card_type,
                                exp_month=exp_month,
                                exp_year=exp_year
                            )
                            session.add(new_card)
                    await session.commit()
                    logger.info(f"Updated subscription for user {user_id}")
                    return True

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
                await session.flush()
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
            except SQLAlchemyError as e:
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
        """Update subscription by Stripe customer ID."""
        async with get_async_session() as session:
            try:
                sub = (await session.execute(
                    select(SubscriptionORM).where(SubscriptionORM.stripe_customer_id == stripe_customer_id)
                )).scalar_one_or_none()
                if not sub:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False
                sub.status = status
                if current_period_end:
                    sub.current_period_end = current_period_end

                if all([card_last4, card_type, exp_month, exp_year]):
                    card = (await session.execute(
                        select(CardDetailsORM).where(CardDetailsORM.subscription_id == sub.id)
                    )).scalar_one_or_none()
                    if card:
                        card.card_last4 = card_last4
                        card.card_type = card_type
                        card.exp_month = exp_month
                        card.exp_year = exp_year
                    else:
                        new_card = CardDetailsORM(
                            subscription_id=sub.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year
                        )
                        session.add(new_card)
                await session.commit()
                logger.info(f"Updated subscription for Stripe customer {stripe_customer_id}")
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error updating subscription: {e}", exc_info=True)
                return False

    async def get_subscription(self, user_id: int) -> Optional[Tuple[SubscriptionORM, Optional[CardDetailsORM]]]:
        """Return user's subscription and card details as domain models."""
        async with get_async_session() as session:
            try:
                sub_orm = (await session.execute(
                    select(SubscriptionORM).where(SubscriptionORM.user_id == user_id)
                )).scalar_one_or_none()
                if not sub_orm:
                    return None

                sub_domain = Subscription(
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

                card_orm = (await session.execute(
                    select(CardDetailsORM).where(CardDetailsORM.subscription_id == sub_orm.id)
                )).scalar_one_or_none()

                card_domain = None
                if card_orm:
                    card_domain = CardDetailsORM(
                        id=card_orm.id,
                        subscription_id=card_orm.subscription_id,
                        card_last4=card_orm.card_last4,
                        card_type=card_orm.card_type,
                        exp_month=card_orm.exp_month,
                        exp_year=card_orm.exp_year,
                        created_at=card_orm.created_at
                    )
                return (sub_domain, card_domain)
            except SQLAlchemyError as e:
                logger.error(f"Error getting subscription: {e}", exc_info=True)
                return None

    async def update_subscription_status(self, stripe_customer_id: str, new_status: str) -> bool:
        """Update subscription status by Stripe customer ID."""
        async with get_async_session() as session:
            try:
                sub = (await session.execute(
                    select(SubscriptionORM).where(SubscriptionORM.stripe_customer_id == stripe_customer_id)
                )).scalar_one_or_none()
                if not sub:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False
                sub.status = new_status
                await session.commit()
                logger.info(f"Updated subscription status to {new_status} for customer {stripe_customer_id}")
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Error updating subscription status: {e}", exc_info=True)
                return False

    async def is_premium_user(self, user_id: int) -> bool:
        """Check if a user has an active premium subscription."""
        async with get_async_session() as session:
            try:
                sub = (await session.execute(
                    select(SubscriptionORM).where(
                        SubscriptionORM.user_id == user_id,
                        SubscriptionORM.status.in_(["active", "trialing"])
                    )
                )).scalar_one_or_none()
                return sub is not None
            except SQLAlchemyError as e:
                logger.error(f"Error checking premium status for user {user_id}: {e}", exc_info=True)
                return False
