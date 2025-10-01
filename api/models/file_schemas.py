"""
Pydantic schemas for file-related requests and responses.
These models are used for API request/response validation and serialization.
"""
from datetime import datetime
from typing import List, Dict, Optional, TypeVar, Generic
from pydantic import BaseModel, Field
from pydantic.generics import GenericModel

# Generic type for paginated responses
T = TypeVar('T')

class PaginatedResponse(GenericModel, Generic[T]):
    """Generic paginated response model."""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int

class FileBase(BaseModel):
    """Base file schema with common fields."""
    file_name: str
    file_url: str
    file_type: str
    status: str

class FileCreate(FileBase):
    """Schema for creating a new file."""
    pass

class FileUpdate(BaseModel):
    """Schema for updating an existing file."""
    file_name: Optional[str] = None
    status: Optional[str] = None

class FileResponse(FileBase):
    """Response schema for file data."""
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FileUploadResponse(FileBase):
    """Response schema for file uploads."""
    id: int

    class Config:
        from_attributes = True

class YouTubeURLRequest(BaseModel):
    """Request schema for YouTube URL processing."""
    url: str
    type: str = "youtube"
    language: Optional[str] = "en"
    comprehension_level: Optional[str] = "beginner"

class UploadResponse(BaseModel):
    """Response schema for file upload operations."""
    message: str
    files: List[FileUploadResponse] = []
    file_id: Optional[int] = None  # For backward compatibility
    file_name: Optional[str] = None  # For backward compatibility
    job_id: Optional[str] = None  # For background job tracking
