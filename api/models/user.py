from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID, uuid4

class UserBase(BaseModel):
    """Base user model with common fields"""
    email: str
    full_name: Optional[str] = None
    is_active: bool = True
    is_superuser: bool = False
    preferences: Dict[str, Any] = Field(default_factory=dict)

class UserCreate(UserBase):
    """User creation model with required fields"""
    password: str

class UserUpdate(BaseModel):
    """User update model with optional fields"""
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    preferences: Optional[Dict[str, Any]] = None

class UserInDBBase(UserBase):
    """Base model for user stored in database"""
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }

class User(UserInDBBase):
    """User model for API responses"""
    pass

class UserInDB(UserInDBBase):
    """User model for database operations"""
    hashed_password: str

# Helper function to update timestamps
def update_timestamps(user: 'UserInDB') -> 'UserInDB':
    """Update the updated_at timestamp"""
    user.updated_at = datetime.utcnow()
    return user
