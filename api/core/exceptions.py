"""
Custom exceptions for SynTextAI.

This module defines application-specific exceptions that can be raised during
normal operation. These exceptions are designed to provide more context
about errors that occur during processing.
"""

class SynTextAIError(Exception):
    """Base exception for all SynTextAI-specific exceptions."""
    def __init__(self, message: str, status_code: int = 500, **kwargs):
        self.message = message
        self.status_code = status_code
        self.details = kwargs
        super().__init__(self.message)

class TranscriptionError(SynTextAIError):
    """Raised when an error occurs during audio transcription."""
    def __init__(self, message: str, file_path: str = None, **kwargs):
        super().__init__(
            message=message,
            status_code=400,
            file_path=file_path,
            **kwargs
        )

class FileProcessingError(SynTextAIError):
    """Raised when an error occurs during file processing."""
    def __init__(self, message: str, file_id: str = None, **kwargs):
        super().__init__(
            message=message,
            status_code=400,
            file_id=file_id,
            **kwargs
        )

class InvalidInputError(SynTextAIError):
    """Raised when invalid input is provided to a function."""
    def __init__(self, message: str, field: str = None, **kwargs):
        super().__init__(
            message=message,
            status_code=422,  # Unprocessable Entity
            field=field,
            **kwargs
        )

class ResourceNotFoundError(SynTextAIError):
    """Raised when a requested resource is not found."""
    def __init__(self, resource_type: str, resource_id: str = None, **kwargs):
        message = f"{resource_type} not found"
        if resource_id:
            message += f" with ID '{resource_id}'"
        super().__init__(
            message=message,
            status_code=404,
            resource_type=resource_type,
            resource_id=resource_id,
            **kwargs
        )

class AuthenticationError(SynTextAIError):
    """Raised when authentication fails."""
    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(
            message=message,
            status_code=401,
            **kwargs
        )

class AuthorizationError(SynTextAIError):
    """Raised when a user is not authorized to perform an action."""
    def __init__(self, message: str = "Not authorized", **kwargs):
        super().__init__(
            message=message,
            status_code=403,
            **kwargs
        )

class RateLimitExceededError(SynTextAIError):
    """Raised when a rate limit is exceeded."""
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = None, **kwargs):
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        
        super().__init__(
            message=message,
            status_code=429,
            headers=headers,
            **kwargs
        )

class ServiceUnavailableError(SynTextAIError):
    """Raised when a required service is unavailable."""
    def __init__(self, service_name: str, **kwargs):
        super().__init__(
            message=f"Service unavailable: {service_name}",
            status_code=503,
            service_name=service_name,
            **kwargs
        )
