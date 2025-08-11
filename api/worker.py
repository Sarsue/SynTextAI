#!/usr/bin/env python
"""
SynText AI File Processing Worker

This standalone worker script processes files asynchronously by:
1. Querying the database for files with 'uploaded' status
2. Processing files in parallel with controlled concurrency using the Agent Service
3. Updating file statuses to track progress
4. Sending real-time notifications via WebSocket

Run this worker in a separate process from the API server
for scalable background processing.
"""

import asyncio
import logging
import os
import sys
import signal
import aiohttp
from datetime import datetime
from typing import Dict, Any, List, Optional

# Configure Python path and environment variables
try:
    # First try to import setup_paths normally
    import setup_paths  # noqa: F401
except ImportError:
    # If that fails, try adding the project root to the path
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    print(f"[DEBUG] setup_paths import failed, adding {project_root} to sys.path")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    # Try importing again
    try:
        import setup_paths  # noqa: F401
        print("[DEBUG] Successfully imported setup_paths after path adjustment")
    except ImportError as e:
        print(f"[WARNING] Could not import setup_paths: {e}")
        print("[WARNING] Continuing without setup_paths, environment variables may not be loaded")

# Standard library imports
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

# Third-party imports
import aiohttp

# Application imports
from api.services.agent_service import AgentService
from api.services.llm_service import LLMService
from api.services.embedding_service import EmbeddingService

# WebSocket imports
try:
    from api.websocket.websocket_manager import WebSocketManager
except ImportError:
    print("Warning: WebSocket manager not found. Real-time updates will be disabled.")
    WebSocketManager = None

# Verify required database configuration
required_db_vars = [
    'DATABASE_NAME',
    'DATABASE_USER',
    'DATABASE_PASSWORD',
    'DATABASE_HOST',
    'DATABASE_PORT'
]

missing_vars = [var for var in required_db_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(
        f"Missing required database configuration: {', '.join(missing_vars)}"
    )

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('syntextai-worker')

# Configuration
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
INITIAL_POLL_INTERVAL = 10  # Start with 10 seconds
MAX_POLL_INTERVAL = 300     # 5 minutes maximum
POLL_BACKOFF_FACTOR = 1.5   # 1.5x backoff factor
API_NOTIFY_URL = os.getenv("API_NOTIFY_URL", "http://syntextaiapp:3000/api/v1/internal/notify-client")

# Initialize services
llm_service = LLMService()
embedding_service = EmbeddingService()
agent_service = AgentService()

# WebSocket manager is optional
websocket_manager = None
try:
    from api.websocket.websocket_manager import WebSocketManager
    websocket_manager = WebSocketManager()
    logger.info("WebSocket manager initialized successfully")
except (ImportError, Exception) as e:
    logger.warning(f"WebSocket manager not available: {str(e)}. Real-time updates will be disabled.")
    websocket_manager = None

async def send_notification_to_api(user_gc_id: str, event_type: str, data: dict):
    """Send a notification to the main API via HTTP POST"""
    payload = {
        "user_gc_id": user_gc_id,
        "event_type": event_type,
        "data": data
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_NOTIFY_URL, json=payload) as response:
                if response.status == 200:
                    logger.info(f"Sent notification for {event_type} to user {user_gc_id} via API")
                else:
                    logger.error(f"Failed to send notification via API. Status: {response.status}, Response: {await response.text()}")
    except Exception as e:
        logger.error(f"Exception while sending notification to API: {e}")

# Global semaphore for limiting concurrent file processing
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# Track the current poll interval
current_poll_interval = INITIAL_POLL_INTERVAL

# Track running tasks to ensure graceful shutdown
running_tasks = []
shutdown_event = asyncio.Event()


def get_repository_manager():
    """Get a RepositoryManager instance with proper database configuration"""
    from api.repositories.repository_manager import RepositoryManager
    
    # Get database configuration from environment variables
    database_config = {
        'dbname': os.getenv("DATABASE_NAME"),
        'user': os.getenv("DATABASE_USER"),
        'password': os.getenv("DATABASE_PASSWORD"),
        'host': os.getenv("DATABASE_HOST"),
        'port': os.getenv("DATABASE_PORT"),
    }
    
    # Construct the URL from individual components
    database_url = (
        f"postgresql://{database_config['user']}:{database_config['password']}@"
        f"{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
    )
    
    return RepositoryManager(database_url=database_url)

async def update_file_status(file_id: int, status: str, error: str = None) -> None:
    """
    Update file status in the database using SQLAlchemy ORM with proper session handling.
    
    Args:
        file_id: The ID of the file to update
        status: The new status to set
        error: Optional error message to store
    """
    store = None
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            from sqlalchemy.exc import SQLAlchemyError, OperationalError
            from models import File
            
            store = get_repository_manager()
            
            with store.file_repo.get_unit_of_work() as uow:
                try:
                    # Get the file with row-level locking to prevent concurrent updates
                    file = uow.session.query(File).filter(
                        File.id == file_id
                    ).with_for_update(skip_locked=True).first()
                    
                    if not file:
                        logger.error(f"File with ID {file_id} not found")
                        return
                    
                    # Update status and error message if provided
                    file.processing_status = status
                    if error is not None:
                        file.error_message = error
                    
                    # Update the updated_at timestamp
                    file.updated_at = datetime.utcnow()
                    
                    uow.session.commit()
                    logger.info(f"Successfully updated file {file_id} status to {status}")
                    return
                    
                except (SQLAlchemyError, OperationalError) as e:
                    uow.session.rollback()
                    if attempt == max_retries - 1:  # Last attempt
                        logger.error(f"Database error updating file {file_id} status after {max_retries} attempts: {str(e)}")
                        raise
                    
                    # Exponential backoff before retry
                    sleep_time = retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Database error updating file {file_id} status (attempt {attempt + 1}/{max_retries}). "
                        f"Retrying in {sleep_time}s. Error: {str(e)}"
                    )
                    await asyncio.sleep(sleep_time)
                
                except Exception as e:
                    if attempt == max_retries - 1:  # Last attempt
                        logger.exception(f"Failed to update file {file_id} status after {max_retries} attempts: {str(e)}")
                        # Re-raise only on the last attempt
                        raise
                    continue
        finally:
            # Clean up resources
            if store and hasattr(store, 'close'):
                try:
                    store.close()
                except Exception as e:
                    logger.warning(f"Error cleaning up repository manager in update_file_status: {str(e)}")

# Import using absolute path since this file is run as a script

async def process_file(
    file_id: int, 
    user_id: int, 
    filename: str,
    file_url: str,
    user_gc_id: Optional[str] = None,
    firebase_token: Optional[str] = None,
    language: str = "English", 
    comprehension_level: str = "Beginner"
) -> Optional[Dict[str, Any]]:
    """
    Process a file in the background.
    
    Args:
        file_id: ID of the file to process
        user_id: ID of the user who owns the file
        filename: Name of the file
        file_url: URL of the file in storage
        user_gc_id: Google Cloud user ID (optional, will be fetched from token if not provided)
        firebase_token: Firebase authentication token (required if user_gc_id is not provided)
        language: Language for processing
        comprehension_level: User's comprehension level
        
    Returns:
        Dictionary with processing results or None if processing failed
    """
    # Since all uploads require authentication, user_gc_id should always be available
    if not user_gc_id:
        error_msg = "User authentication information is missing. This should not happen for authenticated uploads."
        logger.error(f"{error_msg} File ID: {file_id}, User ID: {user_id}")
        await update_file_status(file_id, "failed", error_msg)
        return None
    """
    Process a single file using the Agent Service.
    
    This function handles the complete file processing pipeline including:
    - Status updates
    - File type detection
    - Agent-based processing
    - Error handling and notifications
    - Resource cleanup
    
    Args:
        file_id: The ID of the file to process
        user_id: The ID of the user who owns the file
        user_gc_id: The Google Cloud ID of the user
        filename: The name of the file
        file_url: The URL where the file is stored
        language: The language of the file content (default: "English")
        comprehension_level: The user's comprehension level (default: "Beginner")
        
    Returns:
        Optional[Dict[str, Any]]: The processing result if successful, None otherwise
    """
    # Acquire semaphore to limit concurrent processing
    await semaphore.acquire()
    logger.info(f"Acquired semaphore for file {file_id}")
    
    # Initialize result and track resources
    result = None
    store = None
    
    try:
        # Get repository manager with proper session handling
        store = get_repository_manager()
        
        # Update status to processing
        await update_file_status(
            file_id=file_id,
            status='processing',
            error=None
        )
        
        # Send processing started notification
        await websocket_manager.broadcast(
            user_gc_id,
            "file_processing_started",
            {"file_id": file_id, "filename": filename}
        )
        
        # Determine if this is a YouTube URL
        is_youtube = any(s in file_url.lower() for s in ['youtube.com', 'youtu.be'])
        logger.info(f"Processing {'YouTube' if is_youtube else 'file'}: {filename}")
        
        # Process the file using the agent service
        try:
            if is_youtube:
                result = await agent_service.process_youtube_video(
                    user_id=user_id,
                    user_gc_id=user_gc_id,
                    file_id=file_id,
                    video_url=file_url,
                    language=language,
                    comprehension_level=comprehension_level
                )
            else:
                result = await agent_service.process_uploaded_file(
                    user_id=user_id,
                    user_gc_id=user_gc_id,
                    file_id=file_id,
                    filename=filename,
                    file_url=file_url,
                    language=language,
                    comprehension_level=comprehension_level
                )
            
            # Update status to completed
            await update_file_status(
                file_id=file_id,
                status='completed',
                error=None
            )
            
            # Send success notification
            await websocket_manager.broadcast(
                user_gc_id,
                "file_processing_completed",
                {
                    "file_id": file_id,
                    "filename": filename,
                    "result": {"status": "success"}
                }
            )
            
            logger.info(f"Successfully processed file {filename} (ID: {file_id})")
            return result
            
        except Exception as proc_error:
            error_msg = f"Error in agent service processing: {str(proc_error)}"
            logger.exception(error_msg)
            raise proc_error
        
    except Exception as e:
        error_msg = f"Error processing file {file_id}: {str(e)}"
        logger.exception(error_msg)
        
        # Update status to failed
        try:
            await update_file_status(
                file_id=file_id,
                status='failed',
                error=error_msg[:500]  # Truncate error message to avoid DB issues
            )
            
            # Send error notification
            await websocket_manager.broadcast(
                user_gc_id,
                "file_processing_failed",
                {
                    "file_id": file_id,
                    "filename": filename,
                    "error": str(e)[:500]  # Truncate error message
                }
            )
        except Exception as update_err:
            logger.error(f"Failed to update file status to failed: {str(update_err)}")
            
        return None
        
    finally:
        # Always release the semaphore
        semaphore.release()
        logger.info(f"Released semaphore for file {file_id}")
        
        # Clean up resources
        if store and hasattr(store, 'close'):
            try:
                store.close()
            except Exception as e:
                logger.warning(f"Error cleaning up repository manager: {str(e)}")


async def fetch_pending_files() -> List[Dict[str, Any]]:
    """
    Fetch files with 'uploaded' status from the database and mark them as processing.
    Uses a transaction to ensure atomicity and proper connection handling.
    """
    from api.models.orm_models import File
    from sqlalchemy.orm import joinedload
    
    pending_files = []
    
    try:
        # Use the shared repository manager with proper session handling
        store = get_repository_manager()
        
        # Get a new session
        with store.file_repo.get_unit_of_work() as uow:
            try:
                # Start a transaction
                files_to_process = uow.session.query(File).options(
                    joinedload(File.user, innerjoin=True)
                ).filter(
                    File.processing_status == 'uploaded'
                ).order_by(
                    File.created_at.asc()
                ).with_for_update(
                    skip_locked=True,
                    of=File  # Only lock the files table
                ).limit(10).all()
                
                if not files_to_process:
                    return []
                
                # Update status to processing
                file_ids = [f.id for f in files_to_process]
                uow.session.query(File).filter(File.id.in_(file_ids)).update(
                    {File.processing_status: 'processing'},
                    synchronize_session=False
                )
                
                # Convert files to list of dictionaries
                # Note: user_gc_id will be None here and will be set when processing the file
                # by looking up the user's token in the session
                for file in files_to_process:
                    pending_files.append({
                        "id": file.id,
                        "file_name": file.file_name,
                        "file_url": file.file_url,
                        "user_id": file.user_id,
                        "created_at": file.created_at.isoformat() if file.created_at else None
                    })
                
                # Commit the transaction
                uow.session.commit()
                logger.info(f"Fetched {len(pending_files)} files for processing")
                
            except Exception as e:
                # Rollback on error
                uow.session.rollback()
                logger.error(f"Error in transaction while fetching files: {str(e)}", exc_info=True)
                # Re-raise to be handled by the outer try/except
                raise
                
    except Exception as e:
        logger.exception(f"Error in fetch_pending_files: {str(e)}")
        # Return empty list to continue processing
        return []
    
    return pending_files


async def worker_loop() -> None:
    """Main worker loop that polls for files and processes them with exponential backoff"""
    global current_poll_interval
    
    while not shutdown_event.is_set():
        try:
            # Fetch pending files
            pending_files = await fetch_pending_files()
            
            if pending_files:
                logger.info(f"Found {len(pending_files)} files to process")
                
                # Reset poll interval since we found work
                if current_poll_interval > INITIAL_POLL_INTERVAL:
                    logger.info(f"Resetting poll interval from {current_poll_interval}s to {INITIAL_POLL_INTERVAL}s")
                    current_poll_interval = INITIAL_POLL_INTERVAL
                
                # Create tasks for each file
                tasks = []
                for file in pending_files:
                    # Create task for processing this file
                    task = asyncio.create_task(
                        process_file(
                            file_id=file["id"],
                            user_id=file["user_id"],
                            filename=file["file_name"],
                            file_url=file["file_url"]
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
                logger.info(f"No pending files found. Next poll in {current_poll_interval:.1f} seconds")
                
                # Calculate next poll interval with exponential backoff
                next_interval = min(
                    current_poll_interval * POLL_BACKOFF_FACTOR,
                    MAX_POLL_INTERVAL
                )
                
                # Only log when the interval changes
                if next_interval > current_poll_interval:
                    logger.info(f"Increasing poll interval to {next_interval:.1f} seconds")
                
                current_poll_interval = next_interval
            
            # Wait before polling again with the current interval
            await asyncio.sleep(current_poll_interval)
            
        except Exception as e:
            logger.exception(f"Error in worker loop: {str(e)}")
            # On error, use the current poll interval before trying again
            await asyncio.sleep(current_poll_interval)


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
