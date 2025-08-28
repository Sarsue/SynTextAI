"""
Application configuration settings.

This module handles loading and validating configuration from environment variables.
"""

import os
from typing import Optional, List, Any
from pydantic import Field, PostgresDsn, field_validator, ConfigDict
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application settings with environment variable configuration."""
    
    # Application settings
    APP_NAME: str = "SynTextAI"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")
    
    # Database settings
    DATABASE_URL: Optional[PostgresDsn] = Field(
        default=None,
        description="PostgreSQL connection string"
    )
    
    # Stripe settings
    STRIPE_SECRET: Optional[str] = Field(
        default=os.getenv("STRIPE_SECRET"),
        description="Stripe API secret key for payment processing"
    )
    STRIPE_PRICE_ID: Optional[str] = Field(
        default=os.getenv("STRIPE_PRICE_ID"),
        description="Stripe price ID for subscriptions"
    )
    STRIPE_ENDPOINT_SECRET: Optional[str] = Field(
        default=os.getenv("STRIPE_ENDPOINT_SECRET"),
        description="Stripe webhook endpoint secret for verifying webhook events"
    )
    
    # File processing
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_CONTENT_LENGTH", "16777216"))  # 16MB
    
    # LLM settings
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4")
    
    # Web search settings
    ENABLE_WEB_SEARCH: bool = os.getenv("ENABLE_WEB_SEARCH", "true").lower() == "true"
    
    # CORS settings
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "https://syntextai.com"]
    
    # Pydantic v2 config
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    
    @field_validator("DATABASE_URL", mode='before')
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info: Any) -> Optional[str]:
        if isinstance(v, str) and v:
            return v
            
        # Build default connection string if not provided
        db_user = os.getenv("DATABASE_USER")
        db_password = os.getenv("DATABASE_PASSWORD")
        db_host = os.getenv("DATABASE_HOST")
        db_name = os.getenv("DATABASE_NAME")
        db_port = os.getenv("DATABASE_PORT", "25060")
        
        if not all([db_user, db_password, db_host, db_name]):
            return None
            
        # Construct the DSN as a string with asyncpg driver
        return f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

# Create settings instance
settings = Settings()

# Initialize Stripe if API key is available
if settings.STRIPE_SECRET:
    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET
    except ImportError:
        import logging
        logging.warning("Stripe package not installed. Payment features will be disabled.")
