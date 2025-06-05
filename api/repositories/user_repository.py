"""
User repository for managing user-related database operations.
"""
from typing import Optional, List, Tuple
import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .base_repository import BaseRepository
from .domain_models import User, Subscription, CardDetails

# Import ORM models from the new models module
from models import User as UserORM
from models import Subscription as SubscriptionORM
from models import CardDetails as CardDetailsORM

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository):
    """Repository for user-related database operations."""
    
    def add_user(self, email: str, username: str) -> Optional[int]:
        """Add a new user to the database.
        
        Args:
            email: User's email address
            username: User's username
            
        Returns:
            int: The ID of the newly created user, or None if creation failed
        """
        with self.get_unit_of_work() as uow:
            try:
                new_user = UserORM(email=email, username=username)
                uow.session.add(new_user)
                # Commit handled by UnitOfWork
                return new_user.id
            except IntegrityError:
                # Rollback handled by UnitOfWork
                logger.error(f"User with email '{email}' or username '{username}' already exists.")
                return None
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error adding user: {e}", exc_info=True)
                return None
    
    def get_user_id_from_email(self, email: str) -> Optional[int]:
        """Get user ID from email.
        
        Args:
            email: User's email address
            
        Returns:
            int: The user ID if found, None otherwise
        """
        with self.get_unit_of_work() as uow:
            user = uow.session.query(UserORM).filter(UserORM.email == email).first()
            return user.id if user else None
    
    def delete_user_account(self, user_id: int) -> bool:
        """Delete a user account and all associated data.
        
        Args:
            user_id: ID of the user to delete
            
        Returns:
            bool: True if the deletion was successful, False otherwise
        """
        with self.get_unit_of_work() as uow:
            try:
                # Check if the user exists
                user = uow.session.query(UserORM).filter(UserORM.id == user_id).first()
                if not user:
                    logger.warning(f"Attempted to delete non-existent user: {user_id}")
                    return False
                
                # The cascade should handle deleting related objects
                uow.session.delete(user)
                # Commit handled by UnitOfWork
                logger.info(f"Successfully deleted user {user_id} with cascade")
                return True
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
                
                # Fallback to direct SQL for cleanup if ORM cascade fails
                # We need a new unit of work for this fallback attempt
                with self.get_unit_of_work() as fallback_uow:
                    try:
                        # Direct deletion query
                        fallback_uow.session.execute(text(f"DELETE FROM users WHERE id = {user_id}"))
                        # Commit handled by UnitOfWork
                        logger.info(f"Deleted user {user_id} using direct SQL after ORM failure")
                        return True
                    except Exception as sql_error:
                        # Rollback handled by UnitOfWork
                        logger.error(f"SQL fallback error deleting user {user_id}: {sql_error}", exc_info=True)
                        return False
    
    def add_or_update_subscription(
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
        with self.get_unit_of_work() as uow:
            try:
                # Check if user exists
                user = uow.session.query(UserORM).filter(UserORM.id == user_id).first()
                if not user:
                    logger.error(f"No user found with ID: {user_id}")
                    return False
                
                # Check if subscription already exists for this user
                existing_sub = uow.session.query(SubscriptionORM).filter(
                    SubscriptionORM.user_id == user_id
                ).first()
                
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
                        # Check if card details exist
                        card_details = uow.session.query(CardDetailsORM).filter(
                            CardDetailsORM.subscription_id == existing_sub.id
                        ).first()
                        
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
                            uow.session.add(new_card)
                    
                    # Commit handled by UnitOfWork
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
                    uow.session.add(new_sub)
                    uow.session.flush()  # To get the ID of the new subscription
                    
                    # Add card details if provided
                    if all([card_last4, card_type, exp_month, exp_year]):
                        new_card = CardDetailsORM(
                            subscription_id=new_sub.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year
                        )
                        uow.session.add(new_card)
                    
                    # Commit handled by UnitOfWork
                    logger.info(f"Created new subscription for user {user_id}")
                    return True
                    
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error adding/updating subscription: {e}", exc_info=True)
                return False
    
    def update_subscription(
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
        with self.get_unit_of_work() as uow:
            try:
                # Find subscription by stripe_customer_id
                subscription = uow.session.query(SubscriptionORM).filter(
                    SubscriptionORM.stripe_customer_id == stripe_customer_id
                ).first()
                
                if not subscription:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False
                
                # Update subscription
                subscription.status = status
                if current_period_end:
                    subscription.current_period_end = current_period_end
                
                # Update card details if provided
                if all([card_last4, card_type, exp_month, exp_year]):
                    # Check if card details exist
                    card_details = uow.session.query(CardDetailsORM).filter(
                        CardDetailsORM.subscription_id == subscription.id
                    ).first()
                    
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
                        uow.session.add(new_card)
                
                # Commit handled by UnitOfWork
                logger.info(f"Updated subscription for customer {stripe_customer_id}")
                return True
                
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error updating subscription: {e}", exc_info=True)
                return False
    
    def get_subscription(self, user_id: int) -> Optional[Tuple[Subscription, Optional[CardDetails]]]:
        """Get subscription details for a user.
        
        Args:
            user_id: ID of the user
            
        Returns:
            Tuple[Subscription, Optional[CardDetails]]: Subscription and card details if found
        """
        with self.get_unit_of_work() as uow:
            try:
                # Query for subscription
                sub_orm = uow.session.query(SubscriptionORM).filter(
                    SubscriptionORM.user_id == user_id
                ).first()
                
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
                card_details_orm = uow.session.query(CardDetailsORM).filter(
                    CardDetailsORM.subscription_id == sub_orm.id
                ).first()
                
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
                logger.error(f"Error getting subscription: {e}", exc_info=True)
                return None
    
    def update_subscription_status(self, stripe_customer_id: str, new_status: str) -> bool:
        """Update subscription status by Stripe customer ID (for webhooks).
        
        Args:
            stripe_customer_id: Stripe customer ID
            new_status: New subscription status
            
        Returns:
            bool: True if successful, False otherwise
        """
        with self.get_unit_of_work() as uow:
            try:
                # Find subscription by customer ID
                subscription = uow.session.query(SubscriptionORM).filter(
                    SubscriptionORM.stripe_customer_id == stripe_customer_id
                ).first()
                
                if not subscription:
                    logger.error(f"No subscription found for Stripe customer ID: {stripe_customer_id}")
                    return False
                
                # Update status
                subscription.status = new_status
                # Commit handled by UnitOfWork
                logger.info(f"Updated subscription status to {new_status} for customer {stripe_customer_id}")
                return True
                
            except Exception as e:
                # Rollback handled by UnitOfWork
                logger.error(f"Error updating subscription status: {e}", exc_info=True)
                return False
    
    def is_premium_user(self, user_id: int) -> bool:
        """Check if a user has an active premium subscription.
        
        Args:
            user_id: The ID of the user to check
            
        Returns:
            bool: True if the user has an active premium subscription, False otherwise
        """
        with self.get_unit_of_work() as uow:
            try:
                # Check for an active subscription
                subscription = uow.session.query(SubscriptionORM).filter(
                    SubscriptionORM.user_id == user_id,
                    SubscriptionORM.status.in_(["active", "trialing"])
                ).first()
                
                return subscription is not None
                
            except Exception as e:
                logger.error(f"Error checking premium status for user {user_id}: {e}", exc_info=True)
                return False
