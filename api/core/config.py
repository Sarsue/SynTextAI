"""
Application configuration settings.

This module handles loading and validating configuration from environment variables.
"""

import os
import ssl
import logging
from urllib.parse import quote_plus
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings
load_dotenv()
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
    DB_CONNECTION_RETRIES: int = 3  # Number of retry attempts for database connections
    DATABASE_HOST: Optional[str] = None
    DATABASE_PORT: str = "25060"
    DATABASE_NAME: Optional[str] = None
    DATABASE_SSLMODE: str = "require"
    
    # Connection pool settings
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800  # 30 minutes
    DB_POOL_TIMEOUT: int = 30  # seconds
    DB_POOL_PRE_PING: bool = True
    
    # Connection retry settings
    DB_CONNECTION_RETRIES: int = 5
    DB_RETRY_DELAY: float = 5.0  # seconds
    DB_MAX_RETRY_DELAY: float = 30.0  # seconds
    
    # Timeout settings
    DB_STATEMENT_TIMEOUT: int = 30000  # milliseconds
    
    # SQL logging
    SQL_ECHO: bool = False  # Set to True to log all SQL queries
    SQL_ECHO_POOL: bool = False  # Set to True to log SQL pool operations
    DB_CONNECT_TIMEOUT: int = 30  # seconds
    
    # PostgreSQL specific settings
    DB_KEEPALIVES_IDLE: int = 30  # seconds
    DB_KEEPALIVES_INTERVAL: int = 10  # seconds
    DB_KEEPALIVES_COUNT: int = 5
    
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

    @property
    def async_database_url(self) -> str:
        """Get the async database URL with proper escaping and connection parameters."""
        if self.DATABASE_URL:
            # Convert sync URL to async URL if needed
            url = self.DATABASE_URL
            if url.startswith("postgresql://") and "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://")
            return url
            
        # Build URL from components
        user = quote_plus(self.DATABASE_USER or "")
        password = quote_plus(self.DATABASE_PASSWORD or "")
        
        # Base connection parameters
        params = [
            f"sslmode={self.DATABASE_SSLMODE}",
            f"connect_timeout={self.DB_CONNECT_TIMEOUT}",
            f"application_name={self.APP_NAME.lower().replace(' ', '-')}",
            f"keepalives_idle={self.DB_KEEPALIVES_IDLE}",
            f"keepalives_interval={self.DB_KEEPALIVES_INTERVAL}",
            f"keepalives_count={self.DB_KEEPALIVES_COUNT}",
            f"statement_timeout={self.DB_STATEMENT_TIMEOUT}",
        ]
        
        return (
            f"postgresql+asyncpg://{user}:{password}@"
            f"{self.DATABASE_HOST}:{self.DATABASE_PORT}/"
            f"{self.DATABASE_NAME}?{'&'.join(params)}"
        )
        
    def get_engine_options(self) -> Dict[str, Any]:
        """Get SQLAlchemy engine options with connection pooling settings."""
        return {
            "echo": self.DEBUG,
            "future": True,
            "pool_pre_ping": self.DB_POOL_PRE_PING,
            "pool_recycle": self.DB_POOL_RECYCLE,
            "pool_size": self.DB_POOL_SIZE,
            "max_overflow": self.DB_MAX_OVERFLOW,
            "pool_timeout": self.DB_POOL_TIMEOUT,
            "execution_options": {
                "isolation_level": "READ COMMITTED",
                "compiled_cache": None,  # Disable statement caching
            },
        }
        
    def create_engine(self):
        """Create a new async SQLAlchemy engine with proper configuration."""
        from sqlalchemy.ext.asyncio import create_async_engine
        
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
