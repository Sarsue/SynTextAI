#!/usr/bin/env python
"""
SynText AI File Processing Worker

This standalone worker script processes files asynchronously by:
1. Querying the database for files with 'uploaded' status
2. Processing files in parallel with controlled concurrency
3. Updating file statuses to track progress

Run this worker in a separate process from the API server
for scalable background processing.
"""

import asyncio
import logging
import os
import sys
import signal
import time
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Add the parent directory to sys.path to fix imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("syntextai-worker")

# Maximum number of concurrent file processing tasks
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

# Polling interval in seconds
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

# Global semaphore for limiting concurrent file processing
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# Track running tasks to ensure graceful shutdown
running_tasks = []
shutdown_event = asyncio.Event()


async def process_file(file_id: int, user_id: int, user_gc_id: str, filename: str, 
                       file_url: str, language: str = "English", 
                       comprehension_level: str = "Beginner") -> None:
    """Process a single file with concurrency control"""
    
    # Use a semaphore to limit concurrent processing
    async with semaphore:
        if shutdown_event.is_set():
            logger.info(f"Shutdown requested, skipping processing of file {file_id}")
            return
            
        logger.info(f"Starting processing file {filename} (ID: {file_id}) for user {user_id}")
        
        try:
            # Import here to avoid circular imports
            from api.tasks import process_file_data
            
            # Call the existing file processing function
            await process_file_data(
                user_gc_id=user_gc_id,
                user_id=str(user_id),  # Convert to string as expected by process_file_data
                file_id=str(file_id),  # Convert to string as expected by process_file_data
                filename=filename,
                file_url=file_url,
                is_youtube="youtube" in filename.lower() or "youtu.be" in filename.lower(),
                language=language,
                comprehension_level=comprehension_level
            )
            
            logger.info(f"Completed processing file {filename} (ID: {file_id})")
        
        except Exception as e:
            logger.exception(f"Error processing file {file_id}: {str(e)}")
            
            # Update status to FAILED if there was an exception
            try:
                # FileProcessingStatus enum no longer used - using string literals instead
                from api.repositories.repository_manager import RepositoryManager
                
                # Get database URL from environment variables
                database_config = {
                    'dbname': os.getenv("DATABASE_NAME"),
                    'user': os.getenv("DATABASE_USER"),
                    'password': os.getenv("DATABASE_PASSWORD"),
                    'host': os.getenv("DATABASE_HOST"),
                    'port': os.getenv("DATABASE_PORT"),
                }

                DATABASE_URL = (
                    f"postgresql://{database_config['user']}:{database_config['password']}"
                    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
                )
                
                store = RepositoryManager(database_url=DATABASE_URL)
                
                file = store.file_repo.get_file_by_id(file_id)
                if file and file.processing_status != "failed":  # Use string literal instead of enum
                    with store.file_repo.get_unit_of_work() as uow:
                        file.processing_status = "failed"  # Use string literal instead of enum
                        # No need for explicit commit with unit of work pattern
                    logger.info(f"Updated file {file_id} status to failed")
            
            except Exception as ex:
                logger.error(f"Error updating file status to FAILED: {str(ex)}")


async def fetch_pending_files() -> List[Dict[str, Any]]:
    """Fetch files with 'uploaded' status from the database"""
    try:
        # FileProcessingStatus enum no longer used - using string literals instead
        from api.repositories.repository_manager import RepositoryManager
        
        # Get database URL from environment variables
        database_config = {
            'dbname': os.getenv("DATABASE_NAME"),
            'user': os.getenv("DATABASE_USER"),
            'password': os.getenv("DATABASE_PASSWORD"),
            'host': os.getenv("DATABASE_HOST"),
            'port': os.getenv("DATABASE_PORT"),
        }

        DATABASE_URL = (
            f"postgresql://{database_config['user']}:{database_config['password']}"
            f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
        )
        
        store = RepositoryManager(database_url=DATABASE_URL)
        
        # Get files with UPLOADED status
        # Use ORM instead of raw SQL for better safety and maintainability
        from models import File as FileORM, User as UserORM
        
        # Execute ORM query - using proper unit of work pattern
        with store.file_repo.get_unit_of_work() as uow:
            # Join files and users tables using SQLAlchemy ORM
            query = uow.session.query(
                FileORM.id, 
                FileORM.file_name, 
                FileORM.file_url, 
                FileORM.user_id, 
                FileORM.created_at
                # We'll extract gc_id from file_url in Python code instead of SQL
            ).join(
                UserORM, FileORM.user_id == UserORM.id
            ).filter(
                # Use explicit string literal instead of enum
                FileORM.processing_status == "uploaded"
            ).order_by(
                FileORM.created_at.asc()
            ).limit(10)
            
            result = query.all()
        
        # Convert SQLAlchemy result to list of dictionaries and extract gc_id from file_url
        pending_files = []
        for row in result:
            # Extract user_gc_id from file_url if it's not a YouTube URL
            user_gc_id = ''
            if row.file_url and not ('youtube.com' in row.file_url or 'youtu.be' in row.file_url):
                # Extract the folder name which is the gc_id
                # URL structure: https://storage.googleapis.com/bucket-name/{gc_id}/{filename}
                try:
                    # Split by '/' and get the second-to-last segment which should be the gc_id
                    url_parts = row.file_url.split('/')
                    if len(url_parts) >= 2:
                        user_gc_id = url_parts[-2]  # Second-to-last part is gc_id
                    logger.debug(f"Extracted user_gc_id '{user_gc_id}' from URL: {row.file_url}")
                except Exception as e:
                    logger.error(f"Failed to extract user_gc_id from URL: {row.file_url}. Error: {e}")
            
            # Create the file dict with all necessary information
            pending_files.append({
                "id": row.id,
                "file_name": row.file_name,
                "file_url": row.file_url, 
                "user_id": row.user_id,
                "user_gc_id": user_gc_id,  # Use the extracted gc_id
                "created_at": row.created_at
            })
        
        
        return pending_files
        
    except Exception as e:
        logger.exception(f"Error fetching pending files: {str(e)}")
        return []


async def worker_loop() -> None:
    """Main worker loop that polls for files and processes them"""
    while not shutdown_event.is_set():
        try:
            # Fetch pending files
            pending_files = await fetch_pending_files()
            
            if pending_files:
                logger.info(f"Found {len(pending_files)} files to process")
                
                # Create tasks for each file
                tasks = []
                for file in pending_files:
                    # Create task for processing this file
                    task = asyncio.create_task(
                        process_file(
                            file_id=file["id"],
                            user_id=file["user_id"],
                            user_gc_id=file["user_gc_id"],
                            filename=file["file_name"],
                            file_url=file["file_url"],
                        )
                    )
                    tasks.append(task)
                    running_tasks.append(task)
                
                # Wait for all tasks to complete
                completed_tasks, _ = await asyncio.wait(
                    tasks, 
                    return_when=asyncio.ALL_COMPLETED
                )
                
                # Clean up completed tasks
                for task in completed_tasks:
                    if task in running_tasks:
                        running_tasks.remove(task)
            
            else:
                logger.info("No pending files found")
            
            # Wait before polling again
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            logger.exception(f"Error in worker loop: {str(e)}")
            # Wait a bit before trying again
            await asyncio.sleep(POLL_INTERVAL)


def handle_shutdown(sig, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received shutdown signal {sig}")
    
    # Set the shutdown event to stop creating new tasks
    shutdown_event.set()
    
    # Note: we're not forcibly cancelling running tasks
    # They will continue processing but no new tasks will be started
    
    logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")


async def main():
    """Main entry point for the worker"""
    logger.info(f"Starting SynText AI Worker (Max Concurrent Tasks: {MAX_CONCURRENT_TASKS})")
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    # Start the worker loop
    try:
        await worker_loop()
        
    except Exception as e:
        logger.exception(f"Fatal error in worker: {str(e)}")
        
    finally:
        # Wait for remaining tasks to complete on shutdown
        if running_tasks:
            logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")
            await asyncio.wait(running_tasks)
            
        logger.info("SynText AI Worker shutdown complete")


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
