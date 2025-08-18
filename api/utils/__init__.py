"""
Utils package for SynTextAI.

This package contains various utility modules used throughout the application.
"""

# Import functions from utils.py
from .utils import (
    decode_firebase_token,
    get_user_id,
    format_timestamp,
    upload_to_gcs,
    download_from_gcs,
    delete_from_gcs,
    chunk_text
)

# Make these functions available at the package level
__all__ = [
    'decode_firebase_token',
    'get_user_id',
    'format_timestamp',
    'upload_to_gcs',
    'download_from_gcs',
    'delete_from_gcs',
    'chunk_text',
    'language_utils'
]
