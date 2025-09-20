#!/usr/bin/env python3
"""
Test script to verify the database connection using the consolidated async_db module.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from api.models.async_db import (
    get_engine,
    get_session,
    startup as db_startup,
    shutdown as db_shutdown
)
from sqlalchemy import text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_connection():
    """Test the database connection and basic operations."""
    logger.info("Starting database connection test...")
    
    try:
        # Initialize the database connection
        await db_startup()
        
        # Get the engine and test a simple query
        engine = await get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            version = result.scalar()
            logger.info(f"Database version: {version}")
        
        # Test session management
        async with get_session() as session:
            result = await session.execute(text("SELECT current_database()"))
            db_name = result.scalar()
            logger.info(f"Connected to database: {db_name}")
            
            # Test a more complex query
            result = await session.execute(text("SELECT current_timestamp"))
            timestamp = result.scalar()
            logger.info(f"Current database timestamp: {timestamp}")
        
        logger.info("✅ Database connection test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {e}", exc_info=True)
        return False
    finally:
        # Clean up
        await db_shutdown()

if __name__ == "__main__":
    # Run the test
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
