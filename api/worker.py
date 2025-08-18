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

async def update_file_status(file_id: int, status: str, error: Optional[str] = None) -> bool:
    """
    Update file status in the database using the repository pattern.
    
    Args:
        file_id: The ID of the file to update
        status: The new status to set ('uploaded', 'processing', 'completed', 'failed')
        error: Optional error message to include in the update
        
    Returns:
        bool: True if update was successful, False otherwise
    """
    try:
        store = get_repository_manager()
        
        # Use the repository manager to update the file status
        success = await store.update_file_status_async(
            file_id=file_id,
            status=status,
            error_message=error[:500] if error else None  # Truncate error message to avoid DB issues
        )
        
        if success:
            if status == 'failed':
                logger.error(f"Updated file {file_id} status to {status}. Error: {error}")
            else:
                logger.info(f"Updated file {file_id} status to {status}")
        else:
            logger.warning(f"Failed to update file {file_id} status to {status}")
            
        return success
        
    except Exception as e:
        logger.error(f"Error updating file {file_id} status: {str(e)}")
        return False
        
    finally:
        # Ensure resources are cleaned up
        if 'store' in locals():
            try:
                # Repository manager handles its own cleanup
                pass
            except Exception as e:
                logger.warning(f"Error cleaning up repository manager: {str(e)}")

# Import using absolute path since this file is run as a script

async def process_with_retry(process_func, max_retries=3, initial_delay=1):
    """Helper function to retry processing with exponential backoff."""
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await process_func()
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_exception = e
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                logger.warning(f"Attempt {attempt + 1} failed with {str(e)}. Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
    
    # If we get here, all retries failed
    logger.error(f"All {max_retries} attempts failed. Last error: {str(last_exception)}")
    raise last_exception

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
    Process a file in the background with robust error handling and retries.
    
    Args:
        file_id: ID of the file to process
        user_id: ID of the user who owns the file
        filename: Name of the file
        file_url: URL of the file in storage or YouTube URL
        user_gc_id: Google Cloud user ID (required for GCS operations)
        firebase_token: Firebase authentication token (required if user_gc_id not provided)
        language: Language for processing (default: "English")
        comprehension_level: User's comprehension level (default: "Beginner")
        
    Returns:
        Dictionary with processing results or None if processing failed
    """
    # Check if this is a YouTube URL
    is_youtube = any(s in file_url.lower() for s in ['youtube.com', 'youtu.be'])
    
    # For non-YouTube files, we need user_gc_id for GCS operations
    if not is_youtube and not user_gc_id:
        error_msg = "Google Cloud user ID is required for non-YouTube files. This should not happen for authenticated uploads."
        logger.error(f"{error_msg} File ID: {file_id}, User ID: {user_id}")
        await update_file_status(file_id, "failed", error_msg)
        return None

    # Acquire semaphore to limit concurrent processing
    await semaphore.acquire()
    logger.info(f"Acquired semaphore for file {file_id}")
    
    # Track resources for cleanup
    store = None
    temp_files = []
    
    try:
        # Get repository manager with proper session handling
        store = get_repository_manager()
        
        # Update status to processing
        await update_file_status(
            file_id=file_id,
            status='processing',
            error=None
        )
        
        # Determine if this is a YouTube URL
        is_youtube = any(s in file_url.lower() for s in ['youtube.com', 'youtu.be'])
        file_type = "youtube" if is_youtube else filename.split('.')[-1].lower()
        
        logger.info(f"Processing {file_type.upper()} file: {filename} (ID: {file_id})")
        
        # Send processing started notification if WebSocket manager is available
        if websocket_manager:
            try:
                await websocket_manager.broadcast(
                    user_gc_id,
                    "file_processing_started",
                    {
                        "file_id": file_id, 
                        "filename": filename,
                        "file_type": file_type
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to send WebSocket notification: {str(e)}")
        else:
            logger.debug("WebSocket manager not available, skipping notification")
        
        # Define processing functions with retry logic
        async def process_youtube():
            logger.info(f"Starting YouTube video processing for {file_url}")
            return await agent_service.process_youtube_video(
                user_id=user_id,
                user_gc_id=user_gc_id,
                file_id=file_id,
                video_url=file_url,
                language=language,
                comprehension_level=comprehension_level
            )
            
        async def process_regular_file():
            logger.info(f"Starting file processing for {filename}")
            return await agent_service.process_uploaded_file(
                user_id=user_id,
                user_gc_id=user_gc_id,
                file_id=file_id,
                filename=filename,
                file_url=file_url,
                language=language,
                comprehension_level=comprehension_level
            )
        
        # Process the file with retry logic
        try:
            result = await process_with_retry(
                process_youtube if is_youtube else process_regular_file,
                max_retries=3,
                initial_delay=2
            )
            
            # Validate the result
            if not result or 'status' not in result:
                raise ValueError("Invalid processing result received from agent service")
                
            # Update status to completed
            await update_file_status(
                file_id=file_id,
                status='completed',
                error=None
            )
            
            # Send success notification
            if websocket_manager:
                try:
                    await websocket_manager.broadcast(
                        user_gc_id,
                        "file_processing_completed",
                        {
                            "file_id": file_id,
                            "filename": filename,
                            "file_type": file_type,
                            "success": True,
                            "result": result
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to send WebSocket completion notification: {str(e)}")
            else:
                logger.debug("WebSocket manager not available, skipping completion notification")
            
            logger.info(f"Successfully processed {file_type} file {filename} (ID: {file_id})")
            return result
            
        except Exception as proc_error:
            error_msg = f"Error processing {file_type} file: {str(proc_error)}"
            logger.exception(error_msg)
            raise proc_error
        
    except Exception as e:
        error_msg = f"Error processing {file_type} file {file_id}: {str(e)}"
        logger.exception(error_msg)
        
        # Update status to failed with detailed error
        try:
            await update_file_status(
                file_id=file_id,
                status='failed',
                error=error_msg[:500]  # Truncate error message to avoid DB issues
            )
            
            # Send error notification
            if websocket_manager:
                try:
                    await websocket_manager.broadcast(
                        user_gc_id,
                        "file_processing_failed",
                        {
                            "file_id": file_id,
                            "filename": filename,
                            "file_type": file_type,
                            "error": str(e)
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to send WebSocket error notification: {str(e)}")
            else:
                logger.debug("WebSocket manager not available, skipping error notification")
            
        except Exception as update_err:
            logger.error(f"Failed to update file status to failed: {str(update_err)}")
            
        return None
        
    finally:
        # Clean up any temporary files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                    logger.debug(f"Cleaned up temporary file: {temp_file}")
            except Exception as e:
                logger.warning(f"Error cleaning up temporary file {temp_file}: {str(e)}")
        
        # Always release the semaphore
        semaphore.release()
        logger.info(f"Released semaphore for file {file_id}")
        
        # Clean up repository manager
        if store and hasattr(store, 'close'):
            try:
                store.close()
            except Exception as e:
                logger.warning(f"Error cleaning up repository manager: {str(e)}")


async def fetch_pending_files() -> List[Dict[str, Any]]:
    """
    Fetch files with 'uploaded' status from the database and mark them as processing.
    Uses repository methods to ensure proper transaction handling and data access.
    """
    try:
        # Use the shared repository manager
        store = get_repository_manager()
        
        # Get pending files - this will mark them as processing in a transaction
        pending_files = store.get_pending_files(limit=10)
        
        if pending_files:
            logger.info(f"Fetched {len(pending_files)} files for processing")
        
        return pending_files
        
    except Exception as e:
        logger.exception(f"Error in fetch_pending_files: {str(e)}")
        # Return empty list to continue processing
        return []


async def process_single_file(file: dict) -> None:
    """Process a single file with proper error handling and cleanup."""
    file_id = file.get("id")
    try:
        file_url = file.get("file_url", "")
        if not file_url:
            raise ValueError("File URL is empty")
        
        # Determine if this is a YouTube URL
        is_youtube = 'youtube.com/watch' in file_url or 'youtu.be/' in file_url
        
        # Only extract gc_id for GCS files (not needed for YouTube)
        user_gc_id = None
        if not is_youtube:
            # For GCS files, extract gc_id from URL (format: gs://bucket-name/gc_id/filename)
            url_parts = file_url.split('/')
            if len(url_parts) < 5:  # gs: + '' + bucket + gc_id + filename
                raise ValueError(f"Invalid GCS URL format: {file_url}")
            
            user_gc_id = url_parts[3]  # 0:gs: 1:'' 2:bucket 3:gc_id 4:filename
            if not user_gc_id:
                raise ValueError(f"Could not extract gc_id from URL: {file_url}")
        
        # Update status to processing
        await update_file_status(file_id, "processing")
        
        # Process the file
        # For YouTube files, we don't need gc_id, but we need to ensure firebase_token is passed if required
        await process_file(
            file_id=file_id,
            user_id=file["user_id"],
            filename=file["file_name"],
            file_url=file_url,
            user_gc_id=user_gc_id,
            # For YouTube, we don't need to pass firebase_token as it's not used in the processing
            # and we don't want to store it in the database
            firebase_token=None,
            language="English",  # Default language
            comprehension_level="Beginner"  # Default comprehension level
        )
        
        # Update status to completed
        await update_file_status(file_id, "completed")
        
    except asyncio.CancelledError:
        logger.warning(f"Processing cancelled for file {file_id}")
        await update_file_status(file_id, "failed", "Processing was cancelled")
        raise
        
    except Exception as e:
        error_msg = f"Error processing file {file_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        await update_file_status(file_id, "failed", str(e))
        
    finally:
        # Clean up any temporary resources
        if 'temp_file' in locals():
            try:
                os.unlink(temp_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")

async def worker_loop() -> None:
    """
    Main worker loop that polls for files and processes them with exponential backoff.
    
    Features:
    - Processes files in parallel up to MAX_CONCURRENT_TASKS
    - Implements exponential backoff when no work is found
    - Handles graceful shutdown
    - Tracks running tasks for proper cleanup
    """
    global current_poll_interval
    
    while not shutdown_event.is_set():
        try:
            # Fetch pending files with a limit to avoid overloading
            pending_files = await fetch_pending_files()
            
            if pending_files:
                logger.info(f"Found {len(pending_files)} files to process")
                
                # Reset poll interval since we found work
                if current_poll_interval > INITIAL_POLL_INTERVAL:
                    logger.info(f"Resetting poll interval from {current_poll_interval}s to {INITIAL_POLL_INTERVAL}s")
                    current_poll_interval = INITIAL_POLL_INTERVAL
                
                # Process files in batches to avoid overloading
                batch_size = min(MAX_CONCURRENT_TASKS, len(pending_files))
                
                for i in range(0, len(pending_files), batch_size):
                    batch = pending_files[i:i + batch_size]
                    
                    # Create and track tasks for this batch
                    tasks = []
                    for file in batch:
                        task = asyncio.create_task(process_single_file(file))
                        tasks.append(task)
                        running_tasks.append(task)
                    
                    # Wait for batch to complete or be cancelled
                    try:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    except asyncio.CancelledError:
                        logger.warning("Worker loop cancelled")
                        raise
                    except Exception as e:
                        logger.error(f"Error in batch processing: {e}", exc_info=True)
                    finally:
                        # Clean up completed tasks
                        for task in tasks:
                            if task in running_tasks:
                                running_tasks.remove(task)
            
            else:
                # No pending files, use exponential backoff
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
                try:
                    await asyncio.wait_for(
                        asyncio.sleep(current_poll_interval),
                        timeout=current_poll_interval + 1
                    )
                except asyncio.TimeoutError:
                    logger.warning("Polling interval timeout")
                except asyncio.CancelledError:
                    logger.info("Worker loop cancelled during sleep")
                    raise
            
        except asyncio.CancelledError:
            logger.info("Worker loop cancelled")
            raise
            
        except Exception as e:
            logger.exception(f"Unexpected error in worker loop: {str(e)}")
            # Use current poll interval before retrying
            await asyncio.sleep(min(5, current_poll_interval))  # Short delay before retry
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
