import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

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
    async def get_user_by_email(self, email: str, session: Optional[AsyncSession] = None) -> Optional[User]:
        async with self._repository_manager.session_scope(session) as s:
            stmt = select(User).where(User.email == email)
            result = await s.execute(stmt)
            return result.scalars().first()

    async def get_user_by_id(self, user_id: int, session: Optional[AsyncSession] = None) -> Optional[User]:
        async with self._repository_manager.session_scope(session) as s:
            stmt = select(User).where(User.id == user_id)
            result = await s.execute(stmt)
            return result.scalars().first()

    async def add_user(self, email: str, name: str, session: Optional[AsyncSession] = None) -> int:
        user = User(email=email, username=name)
        async with self._repository_manager.session_scope(session) as s:
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    async def delete_user_account(self, user_id: int) -> bool:
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("Invalid user_id provided")

        async with self._repository_manager.session_scope() as session:
            try:
                user = await self._get_user_by_id(user_id, session)
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
        async with self._repository_manager.session_scope(session) as s:
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
                    data['payment_methods'].append({
                        'id': card.id,
                        'type': 'card',
                        'card': {
                            'brand': card.card_type,
                            'last4': card.card_last4,
                            'exp_month': card.exp_month,
                            'exp_year': card.exp_year
                        },
                        'stripe_payment_method_id': card.stripe_payment_method_id,
                        'created_at': card.created_at.isoformat(),
                        'updated_at': card.updated_at.isoformat()
                    })
            return data

    async def get_user_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self._get_subscription_base(user_id, include_payment_methods=False)

    async def get_subscription_with_card(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self._get_subscription_base(user_id, include_payment_methods=True)

    async def upsert_subscription(
        self,
        user_id: int,
        status: Union[str, SubscriptionStatus],
        *,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        current_period_end: Optional[Union[str, datetime]] = None,
        card_last4: Optional[str] = None,
        card_brand: Optional[str] = None,
        exp_month: Optional[int] = None,
        exp_year: Optional[int] = None,
        stripe_payment_method_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if isinstance(status, str):
            status = SubscriptionStatus(status.lower())
        if isinstance(current_period_end, str):
            current_period_end = datetime.fromisoformat(current_period_end.replace("Z", "+00:00"))

        card_info_provided = any([card_last4, card_brand, exp_month, exp_year, stripe_payment_method_id])
        if card_info_provided and not all([card_last4, card_brand, exp_month, exp_year]):
            raise ValueError("All card fields must be provided if any are given")

        async with self._repository_manager.session_scope() as session:
            subscription = await self._get_subscription_by_criteria(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                session=session
            )
            now = datetime.now(timezone.utc)

            if subscription:
                subscription.status = status
                if stripe_customer_id:
                    subscription.stripe_customer_id = stripe_customer_id
                if stripe_subscription_id:
                    subscription.stripe_subscription_id = stripe_subscription_id
                if current_period_end:
                    subscription.current_period_end = current_period_end
                subscription.updated_at = now
                session.add(subscription)
            else:
                subscription = Subscription(
                    user_id=user_id,
                    status=status,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    current_period_end=current_period_end,
                    created_at=now,
                    updated_at=now
                )
                session.add(subscription)
                await session.flush()

            if card_info_provided:
                card = subscription.card_details
                if card:
                    card.card_last4 = card_last4
                    card.card_type = card_brand
                    card.exp_month = exp_month
                    card.exp_year = exp_year
                    if stripe_payment_method_id:
                        card.stripe_payment_method_id = stripe_payment_method_id
                    card.updated_at = now
                    session.add(card)
                else:
                    card = CardDetails(
                        subscription_id=subscription.id,
                        card_last4=card_last4,
                        card_type=card_brand,
                        exp_month=exp_month,
                        exp_year=exp_year,
                        stripe_payment_method_id=stripe_payment_method_id,
                        created_at=now,
                        updated_at=now
                    )
                    session.add(card)

            await session.commit()
            return await self._get_subscription_base(user_id, include_payment_methods=card_info_provided)
