"""
Pydantic schemas for user-related models.
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

class UserBase(BaseModel):
    """Base user model with common fields."""
    email: EmailStr
    full_name: Optional[str] = None
    role: UserRole = UserRole.USER
    metadata_: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="metadata")

class UserCreate(UserBase):
    """Schema for creating a new user."""
    firebase_uid: str
    email_verified: bool = False
    
    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    """Schema for updating an existing user."""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    
    class Config:
        from_attributes = True

class UserInDBBase(UserBase):
    """Base model for user data stored in the database."""
    id: int
    firebase_uid: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class UserResponse(UserInDBBase):
    """User model for API responses."""
    pass

class UserInDB(UserInDBBase):
    """User model with sensitive data for internal use.
    
    Note: Soft deletion is implemented by setting the email to 'deleted_{user_id}@deleted.com'.
    This pattern is used to track deleted users while maintaining referential integrity.
    """
    hashed_password: Optional[str] = None
    is_superuser: bool = False
    
    class Config:
        from_attributes = True
