"""
Application configuration settings.

This module handles loading and validating configuration from environment variables.
"""

import os
from typing import Dict, Optional
from pydantic import BaseSettings, Field, PostgresDsn, validator

class Settings(BaseSettings):
    """Application settings with environment variable configuration."""
    
    # Application settings
    APP_NAME: str = "SynTextAI"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")
    
    # Database settings
    DATABASE_URL: PostgresDsn = Field(
        default=os.getenv("DATABASE_URL"),
        description="PostgreSQL connection string"
    )
    
    # Stripe settings
    STRIPE_SECRET: Optional[str] = os.getenv("STRIPE_SECRET")
    
    # File processing
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))  # 16MB
    
    # LLM settings
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4")
    
    # Web search settings
    ENABLE_WEB_SEARCH: bool = os.getenv("ENABLE_WEB_SEARCH", "true").lower() == "true"
    
    # CORS settings
    CORS_ORIGINS: list = ["http://localhost:3000", "https://syntextai.com"]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    @validator("DATABASE_URL", pre=True)
    def assemble_db_connection(cls, v: Optional[str], values: Dict[str, any]) -> str:
        if isinstance(v, str) and v:
            return v
            
        return PostgresDsn.build(
            scheme="postgresql",
            user=os.getenv("DATABASE_USER"),
            password=os.getenv("DATABASE_PASSWORD"),
            host=os.getenv("DATABASE_HOST"),
            path=f"/{os.getenv('DATABASE_NAME') or ''}",
            port=os.getenv("DATABASE_PORT", "5432"),
        )

# Create settings instance
settings = Settings()

# Initialize Stripe if API key is available
if settings.STRIPE_SECRET:
    import stripe
    stripe.api_key = settings.STRIPE_SECRET
