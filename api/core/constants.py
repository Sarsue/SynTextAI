"""
Application-wide constants.

This module contains constants used throughout the SynTextAI application.
"""
from typing import Dict, List, Set, Final, Any

# Language support
LANGUAGE_CODE_MAP: Final[Dict[str, str]] = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
}

# Supported file extensions for different content types
SUPPORTED_FILE_EXTENSIONS: Final[Dict[str, Set[str]]] = {
    "document": {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
}

# Maximum file sizes (in bytes)
MAX_FILE_SIZES: Final[Dict[str, int]] = {
    "document": 50 * 1024 * 1024,  # 50MB
    "audio": 100 * 1024 * 1024,     # 100MB
    "video": 500 * 1024 * 1024,     # 500MB
    "image": 20 * 1024 * 1024,      # 20MB
}

# Default processing timeouts (in seconds)
PROCESSING_TIMEOUTS: Final[Dict[str, int]] = {
    "transcription": 300,      # 5 minutes
    "document_processing": 600,  # 10 minutes
    "llm_generation": 120,     # 2 minutes
    "web_request": 30,         # 30 seconds
}

# User roles and permissions
USER_ROLES: Final[Dict[str, Set[str]]] = {
    "free": {"upload_documents", "view_content"},
    "premium": {"upload_documents", "view_content", "export_content", "api_access"},
    "enterprise": {"upload_documents", "view_content", "export_content", "api_access", "team_management"},
}

# Default pagination settings
DEFAULT_PAGINATION: Final[Dict[str, int]] = {
    "page": 1,
    "per_page": 10,
    "max_per_page": 100,
}

# Cache TTLs (in seconds)
CACHE_TTL: Final[Dict[str, int]] = {
    "user_data": 3600,        # 1 hour
    "document_content": 86400, # 24 hours
    "api_responses": 300,     # 5 minutes
}

# Feature flags
FEATURE_FLAGS: Final[Dict[str, bool]] = {
    "enable_web_search": True,
    "enable_export_pdf": True,
    "enable_team_features": False,
    "enable_analytics": True,
}

# API rate limits (requests per minute)
RATE_LIMITS: Final[Dict[str, int]] = {
    "free": 60,      # 60 requests per minute
    "premium": 300,  # 300 requests per minute
    "enterprise": 1000,  # 1000 requests per minute
}

# Error messages
ERROR_MESSAGES: Final[Dict[str, str]] = {
    "file_too_large": "File size exceeds maximum allowed size of {max_size}MB",
    "unsupported_file_type": "File type '{file_type}' is not supported",
    "processing_timeout": "Processing took too long and was aborted",
    "invalid_language": "Language '{language}' is not supported",
    "resource_not_found": "The requested resource was not found",
    "permission_denied": "You don't have permission to perform this action",
}

# Default model parameters
DEFAULT_MODEL_PARAMS: Final[Dict[str, Any]] = {
    "temperature": 0.7,
    "max_tokens": 1024,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
}

# Webhook events
WEBHOOK_EVENTS: Final[Set[str]] = {
    "file_processed",
    "processing_failed",
    "subscription_updated",
    "user_registered",
}
