import os
import ssl
import logging
from urllib.parse import quote_plus
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv
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
    DATABASE_HOST: Optional[str] = None
    DATABASE_PORT: str = "25060"
    DATABASE_NAME: Optional[str] = None
    DATABASE_SSLMODE: str = "require"
    
    # SSL Configuration
    DB_SSL: bool = True
    DB_SSL_VERIFY: bool = True
    DB_SSL_ROOT_CERT: Optional[str] = "/app/api/config/ca-certificate.crt"
    DB_SSL_CERT: Optional[str] = None
    DB_SSL_KEY: Optional[str] = None

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
    DB_CONNECT_TIMEOUT: int = 30  # seconds
    DB_STATEMENT_TIMEOUT: int = 30000  # milliseconds

    # SQL logging
    SQL_ECHO: bool = False
    SQL_ECHO_POOL: bool = False

    # PostgreSQL keepalives
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

    # Web search
    ENABLE_WEB_SEARCH: bool = True

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "https://syntextai.com"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

    def get_ssl_context(self):
        """Create SSL context with proper verification settings."""
        if not self.DB_SSL:
            logger.info("SSL is disabled for database connection")
            return None
            
        try:
            ssl_context = ssl.create_default_context()
            
            # Load CA certificate if available
            if self.DB_SSL_ROOT_CERT and os.path.exists(self.DB_SSL_ROOT_CERT):
                logger.info(f"Using CA certificate: {self.DB_SSL_ROOT_CERT}")
                ssl_context.load_verify_locations(cafile=self.DB_SSL_ROOT_CERT)
            
            # Load client certificate if available
            if self.DB_SSL_CERT and os.path.exists(self.DB_SSL_CERT):
                ssl_context.load_cert_chain(
                    certfile=self.DB_SSL_CERT,
                    keyfile=self.DB_SSL_KEY if self.DB_SSL_KEY else None
                )
            
            # Configure verification
            if not self.DB_SSL_VERIFY:
                logger.warning("SSL certificate verification is disabled")
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ssl_context.check_hostname = self.DATABASE_SSLMODE == "verify-full"
                ssl_context.verify_mode = ssl.CERT_REQUIRED
                
            return ssl_context
            
        except Exception as e:
            logger.error(f"Error creating SSL context: {e}", exc_info=True)
            if not self.DB_SSL_VERIFY:
                logger.warning("Proceeding without SSL verification due to error")
                return None
            raise

    @property
    def async_database_url(self) -> str:
        """Build asyncpg-compatible database URL."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://") and "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://")
            return f"{url}?timeout={self.DB_CONNECT_TIMEOUT}"

        user = quote_plus(self.DATABASE_USER or "")
        password = quote_plus(self.DATABASE_PASSWORD or "")

        ssl_context = self.get_ssl_context()
        base_params = [f"timeout={self.DB_CONNECT_TIMEOUT}"]
        if not ssl_context and self.DATABASE_SSLMODE:
            base_params.append(f"sslmode={self.DATABASE_SSLMODE}")

        param_str = f"?{'&'.join(base_params)}" if base_params else ""

        return (
            f"postgresql+asyncpg://{user}:{password}@"
            f"{self.DATABASE_HOST}:{self.DATABASE_PORT}/"
            f"{self.DATABASE_NAME}{param_str}"
        )

    def get_engine_options(self) -> Dict[str, Any]:
        """Return SQLAlchemy engine options for pooling."""
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
                "compiled_cache": None,
            },
        }

    def create_engine(self):
        """Create async SQLAlchemy engine for asyncpg."""
        from sqlalchemy.ext.asyncio import create_async_engine

        db_url = self.async_database_url
        ssl_context = self.get_ssl_context()

        server_settings = {
            "application_name": self.APP_NAME.lower().replace(" ", "-"),
            "statement_timeout": str(self.DB_STATEMENT_TIMEOUT),
        }

        connect_args = {
            "server_settings": server_settings,
            "timeout": self.DB_CONNECT_TIMEOUT,  # asyncpg arg
        }
        if ssl_context:
            connect_args["ssl"] = ssl_context

        logger.info(f"Connecting to database: {self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}")

        return create_async_engine(
            db_url,
            echo=self.SQL_ECHO,
            echo_pool=self.SQL_ECHO_POOL,
            pool_pre_ping=self.DB_POOL_PRE_PING,
            pool_recycle=self.DB_POOL_RECYCLE,
            pool_size=self.DB_POOL_SIZE,
            max_overflow=self.DB_MAX_OVERFLOW,
            pool_timeout=self.DB_POOL_TIMEOUT,
            connect_args=connect_args,
            execution_options={
                "isolation_level": "READ COMMITTED",
                "compiled_cache": None,
            },
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
        logger.warning("Stripe package not installed. Payment features will be disabled.")
