from pydantic import BaseModel
from typing import List, Optional, TypeVar, Generic
from datetime import datetime

T = TypeVar('T')

class FileResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    gcs_path: str
    status: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class FileListResponse(BaseModel):
    files: List[FileResponse]

class UploadResponse(BaseModel):
    file_id: int
    filename: str
    status: str
    message: str = "File processing started"

class StatusResponse(BaseModel):
    status: str
    message: str

class StandardResponse(BaseModel, Generic[T]):
    """Standardized API response format for all endpoints"""
    status: str = "success"
    data: T
    count: Optional[int] = None
    message: Optional[str] = None
