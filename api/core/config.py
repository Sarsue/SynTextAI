"""
Application configuration settings.

This module handles loading and validating configuration from environment variables.
"""

import os
import ssl
import logging
from urllib.parse import quote_plus
from typing import List, Optional, Dict, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    """Application settings with environment variable configuration."""
    
    # Application settings
    APP_NAME: str = "SynTextAI"
    DEBUG: bool = False
    
    # Database settings
    DATABASE_URL: Optional[str] = None
    DATABASE_USER: Optional[str] = None
    DATABASE_PASSWORD: Optional[str] = None
    DATABASE_HOST: Optional[str] = None
    DATABASE_PORT: str = "25060"
    DATABASE_NAME: Optional[str] = None
    DATABASE_SSLMODE: str = "require"
    
    # Stripe settings
    STRIPE_SECRET: Optional[str] = None
    STRIPE_PRICE_ID: Optional[str] = None
    STRIPE_ENDPOINT_SECRET: Optional[str] = None
    
    # File processing
    UPLOAD_FOLDER: str = "uploads"
    MAX_CONTENT_LENGTH: int = 16 * 1024 * 1024  # 16MB
    
    # LLM settings
    LLM_MODEL: str = "gpt-4"
    
    # Web search settings
    ENABLE_WEB_SEARCH: bool = True
    
    # CORS settings
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "https://syntextai.com"]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"
    
    def get_ssl_context(self):
        """Create SSL context based on DATABASE_SSLMODE."""
        sslmode = (self.DATABASE_SSLMODE or "require").lower()
        
        if sslmode == "disable":
            logger.info("SSL is disabled for database connection")
            return False
            
        logger.info(f"Creating SSL context with mode: {sslmode}")
        
        # Create a default SSL context
        ssl_context = ssl.create_default_context()
        
        # Handle different SSL modes
        if sslmode == "require":
            # Basic SSL without certificate verification
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context
            
        # For verify-ca and verify-full, try to load the CA certificate
        cafile = os.getenv("DATABASE_SSLROOTCERT")
        if not cafile:
            # Try default location
            cafile = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'config',
                'ca-certificate.crt'
            )
            
        if os.path.exists(cafile):
            logger.info(f"Using CA certificate: {cafile}")
            ssl_context.load_verify_locations(cafile)
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            
            if sslmode == "verify-full":
                ssl_context.check_hostname = True
            else:  # verify-ca
                ssl_context.check_hostname = False
                
            return ssl_context
            
        # Fallback to basic SSL if no CA cert found
        logger.warning("CA certificate not found, falling back to basic SSL")
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    def create_engine(self):
        """Create a new async SQLAlchemy engine with proper configuration."""
        from sqlalchemy.ext.asyncio import create_async_engine
        
        # URL-encode the password
        encoded_password = quote_plus(self.DATABASE_PASSWORD or "")
        
        # Build connection string without sslmode
        url = (
            f"postgresql+asyncpg://{self.DATABASE_USER}:{encoded_password}@"
            f"{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        )
        
        # Get SSL context
        ssl_context = self.get_ssl_context()
        connect_args = {}
        if ssl_context:
            connect_args["ssl"] = ssl_context
            
        logger.info(f"Connecting to database: {self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}")
        
        return create_async_engine(
            url,
            echo=True,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        
    async def test_connection(self):
        """Test the database connection."""
        from sqlalchemy import text
        
        engine = self.create_engine()
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                logger.info(f"✅ Connection test result: {result.scalar()}")
                return True
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            return False
        finally:
            await engine.dispose()

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
