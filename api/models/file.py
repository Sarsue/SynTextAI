from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum

class FileProcessingStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"

class FileBase(BaseModel):
    """Base schema for File."""
    file_name: str
    file_url: str
    file_type: Optional[str] = None
    processing_status: FileProcessingStatus = FileProcessingStatus.UPLOADED
    user_id: int

class FileCreate(FileBase):
    """Schema for creating a new File."""
    pass

class FileUpdate(BaseModel):
    """Schema for updating a File."""
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    processing_status: Optional[FileProcessingStatus] = None

class FileInDBBase(FileBase):
    """Base schema for File in database."""
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class File(FileInDBBase):
    """Schema for File."""
    pass

class FileInDB(FileInDBBase):
    """Schema for File in database."""
    pass
