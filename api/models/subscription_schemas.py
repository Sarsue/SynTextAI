"""
Pydantic schemas for subscription-related requests and responses.
These models are used for API request/response validation and serialization.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class CardDetailsResponse(BaseModel):
    """Response model for card details"""
    last4: str
    brand: str
    exp_month: int
    exp_year: int

    class Config:
        from_attributes = True

class SubscriptionResponse(BaseModel):
    """Response model for subscription data"""
    subscription_status: str
    card_last4: Optional[str] = None
    card_brand: Optional[str] = None
    card_exp_month: Optional[int] = None
    card_exp_year: Optional[int] = None
    trial_end: Optional[datetime] = None

    class Config:
        from_attributes = True

class SubscriptionCreate(BaseModel):
    """Schema for creating a new subscription"""
    payment_method_id: str
    price_id: str

class SubscriptionUpdate(BaseModel):
    """Schema for updating an existing subscription"""
    status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: Optional[bool] = None

class WebhookEvent(BaseModel):
    """Schema for Stripe webhook events"""
    id: str
    type: str
    data: Dict[str, Any]
    created: int
