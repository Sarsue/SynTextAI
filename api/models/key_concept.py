"""
Pydantic models for Key Concept operations.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class KeyConceptBase(BaseModel):
    """Base schema for Key Concept."""
    file_id: int
    concept_title: str
    concept_explanation: str
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None
    is_custom: bool = False

class KeyConceptCreate(KeyConceptBase):
    """Schema for creating a new Key Concept."""
    pass

class KeyConceptUpdate(BaseModel):
    """Schema for updating a Key Concept."""
    concept_title: Optional[str] = None
    concept_explanation: Optional[str] = None
    source_page_number: Optional[int] = None
    source_video_timestamp_start_seconds: Optional[int] = None
    source_video_timestamp_end_seconds: Optional[int] = None
    is_custom: Optional[bool] = None

class KeyConceptInDBBase(KeyConceptBase):
    """Base schema for Key Concept in database."""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class KeyConcept(KeyConceptInDBBase):
    """Schema for Key Concept."""
    pass

class KeyConceptInDB(KeyConceptInDBBase):
    """Schema for Key Concept in database."""
    pass
