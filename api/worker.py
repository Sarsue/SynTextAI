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

# Standard library imports
import asyncio
import logging
import os
import random
import signal
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Callable, Coroutine

# Third-party imports
import aiohttp

# Configure Python path and environment variables
try:
    # First try to import setup_paths normally
    import setup_paths  # noqa: F401
except ImportError:
    # If that fails, try adding the project root to the path
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

# Application imports
try:
    from api.services.agent_service import AgentService
    from api.agents.ingestion_agent import IngestionAgent, IngestionConfig
    from api.services.llm_service import LLMService
    from api.services.embedding_service import EmbeddingService
except ImportError as e:
    print(f"[WARNING] Could not import application modules: {e}")

# WebSocket imports
try:
    from api.websocket.websocket_manager import WebSocketManager
    websocket_manager = WebSocketManager()
except ImportError:
    print("Warning: WebSocket manager not found. Real-time updates will be disabled.")
    websocket_manager = None

# Configuration
MAX_CONCURRENT_TASKS = int(os.getenv('MAX_CONCURRENT_TASKS', '3'))
INITIAL_POLL_INTERVAL = 5  # seconds
MAX_POLL_INTERVAL = 300  # 5 minutes
POLL_BACKOFF_FACTOR = 1.5
API_NOTIFY_URL = os.getenv("API_NOTIFY_URL", "http://syntextaiapp:3000/api/v1/internal/notify-client")

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.getenv('LOG_FILE', 'worker.log'))
    ]
)
logger = logging.getLogger('syntext_worker')

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

# Initialize ingestion agent with default config
ingestion_agent = IngestionAgent(IngestionConfig())

# WebSocket manager is optional
websocket_manager = None
try:
    from api.websocket_manager import WebSocketManager
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
    """
    Get a RepositoryManager instance with proper database configuration.
    
    Returns:
        RepositoryManager: A new repository manager instance
    """
    from api.repositories.repository_manager import get_repository_manager as get_repo_manager
    from api.core.config import settings
    
    try:
        # Get database URL from settings
        db_url = settings.DATABASE_URL
        if not db_url:
            # Fallback to environment variable if not in settings
            import os
            db_url = os.getenv('DATABASE_URL')
            if not db_url:
                # Construct from individual components as last resort
                db_url = f"postgresql+asyncpg://{settings.DATABASE_USER}:{settings.DATABASE_PASSWORD}@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
        
        # Create and return repository manager with the database URL
        return get_repo_manager(database_url=db_url)
        
    except Exception as e:
        logger.error(f"Failed to initialize repository manager: {str(e)}")
        raise

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
    repo_manager = None
    try:
        # Get a new repository manager for this operation
        repo_manager = get_repository_manager()
        
        try:
            # Get the file repository
            file_repo = repo_manager.file_repo
            
            # Update the file status
            success = await file_repo.update_file(
                file_id=file_id,
                status=status,
                error_message=error,
                updated_at=datetime.utcnow()
            )
            
            if success:
                await repo_manager.commit()
                logger.info(f"Updated file {file_id} status to '{status}'")
            else:
                logger.error(f"Failed to update file {file_id} status to '{status}'")
                await repo_manager.rollback()
                
            return success
            
        except Exception as e:
            logger.error(f"Error updating file {file_id} status: {str(e)}", exc_info=True)
            if hasattr(repo_manager, 'rollback'):
                await repo_manager.rollback()
            return False
            
    except Exception as e:
        logger.error(f"Error getting repository manager: {str(e)}", exc_info=True)
        return False
        
    finally:
        # Close the repository manager
        if repo_manager is not None and hasattr(repo_manager, 'close'):
            try:
                await repo_manager.close()
            except Exception as e:
                logger.error(f"Error closing repository manager: {str(e)}", exc_info=True)

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
        repo_manager = get_repository_manager()
        
        # Update status to processing
        try:
            file_repo = repo_manager.file_repo
            success = await file_repo.update_file(
                file_id=file_id,
                processing_status='processing',
                updated_at=datetime.utcnow()
            )
            
            if not success:
                logger.error(f"Failed to update file {file_id} status to 'processing'")
                return None
                
            await repo_manager.commit()
            logger.info(f"Updated file {file_id} status to 'processing'")
        except Exception as e:
            logger.error(f"Error updating file status to 'processing': {str(e)}", exc_info=True)
            if hasattr(repo_manager, 'rollback'):
                await repo_manager.rollback()
            return None
        
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
            # Process the YouTube video using the ingestion agent
            return await ingestion_agent.process({
                "source_type": "youtube",
                "url": file_url,
                "file_id": file_id,
                "metadata": {
                    "user_id": user_id,
                    "language": language,
                    "comprehension_level": comprehension_level
                }
            })
            
        async def process_regular_file():
            logger.info(f"Starting file processing for {filename}")
            # Determine file type from extension
            file_ext = filename.split('.')[-1].lower()
            source_type = "pdf" if file_ext == "pdf" else "text"
            
            # Process the file using the ingestion agent
            return await ingestion_agent.process({
                "source_type": source_type,
                "content": file_url,  # URL to the file
                "file_id": file_id,
                "metadata": {
                    "user_id": user_id,
                    "filename": filename,
                    "language": language,
                    "comprehension_level": comprehension_level
                }
            })
        
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
            file_repo = repo_manager.file_repo
            success = await file_repo.update_file(
                file_id=file_id,
                processing_status='completed',
                updated_at=datetime.utcnow()
            )
            
            if not success:
                logger.error(f"Failed to update file {file_id} status to 'completed'")
                return None
                
            await repo_manager.commit()
            logger.info(f"Updated file {file_id} status to 'completed'")
            
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
            file_repo = repo_manager.file_repo
            success = await file_repo.update_file(
                file_id=file_id,
                processing_status='failed',
                error_message=error_msg[:500],  # Truncate error message to avoid DB issues
                updated_at=datetime.utcnow()
            )
            
            if success:
                await repo_manager.commit()
                logger.error(f"Updated file {file_id} status to 'failed': {error_msg}")
            else:
                logger.error(f"Failed to update file {file_id} status to 'failed'")
                await repo_manager.rollback()
            
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
                except Exception as ws_err:
                    logger.warning(f"Failed to send WebSocket error notification: {str(ws_err)}")
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
        if 'repo_manager' in locals() and repo_manager is not None:
            try:
                if hasattr(repo_manager, 'close') and asyncio.iscoroutinefunction(repo_manager.close):
                    await repo_manager.close()
                    logger.debug("Successfully closed repository manager")
                elif hasattr(repo_manager, 'close'):
                    repo_manager.close()
                    logger.debug("Synchronously closed repository manager")
            except Exception as e:
                logger.warning(f"Error cleaning up repository manager: {str(e)}", exc_info=True)
                store = None


async def fetch_pending_files() -> List[dict]:
    """
    Fetch files with 'uploaded' status from the database and mark them as processing.
    Uses repository methods to ensure proper transaction handling and data access.
    
    Returns:
        List[dict]: List of file data dictionaries with 'uploaded' status
    """
    repo_manager = None
    try:
        # Get a new repository manager for this operation
        repo_manager = get_repository_manager()
        
        # Get the file repository
        file_repo = repo_manager.file_repo
        
        # Get a new session for this operation
        async with repo_manager.get_session() as session:
            try:
                # Find all files with status 'uploaded'
                pending_files = await file_repo.get_files_by_status('uploaded')
                
                if not pending_files:
                    logger.debug("No pending files found with status 'uploaded'")
                    return []
                    
                # Get the file IDs for the batch update
                file_ids = [f['id'] for f in pending_files]
                
                # Update status to 'processing' in a single transaction
                success = await file_repo.batch_update_status(file_ids, 'processing')
                
                if success:
                    await session.commit()
                    logger.info(f"Marked {len(pending_files)} files as 'processing'")
                    return pending_files
                else:
                    await session.rollback()
                    logger.error("Failed to update file statuses to 'processing'")
                    return []
                    
            except Exception as e:
                await session.rollback()
                logger.error(f"Error fetching or updating pending files: {str(e)}", exc_info=True)
                return []
                
    except Exception as e:
        logger.error(f"Error getting repository manager: {str(e)}", exc_info=True)
        return []
        
    finally:
        # Close the repository manager
        if repo_manager is not None and hasattr(repo_manager, 'close'):
            try:
                if asyncio.iscoroutinefunction(repo_manager.close):
                    await repo_manager.close()
                else:
                    repo_manager.close()
            except Exception as close_error:
                logger.error(f"Error closing repository manager: {str(close_error)}", exc_info=True)


async def process_single_file(file: dict) -> None:
    """Process a single file with proper error handling and cleanup.
    
    Args:
        file: Dictionary containing file information with keys:
            - id: File ID in the database
            - user_id: ID of the user who owns the file
            - file_name: Name of the file
            - file_url: URL of the file in storage or YouTube URL
            - status: Current status of the file
    """
    file_id = file.get('id')
    user_id = file.get('user_id')
    filename = file.get('file_name')
    file_url = file.get('file_url')
    
    if not all([file_id, user_id, filename, file_url]):
        logger.error(f"Invalid file data: {file}")
        return
    
    logger.info(f"Starting processing for file {file_id}: {filename}")
    
    # Get repository manager at the start
    repo_manager = get_repository_manager()
    
    try:
        # Get the file repository
        file_repo = repo_manager.file_repo
        
        # Update status to processing
        success = await file_repo.update_file(
            file_id=file_id,
            status='processing',
            updated_at=datetime.utcnow()
        )
        
        if not success:
            logger.error(f"Failed to update file {file_id} status to 'processing'")
            return
            
        # Commit the status update
        await repo_manager.commit()
        
        # Process the file
        result = await process_file(
            file_id=file_id,
            user_id=user_id,
            filename=filename,
            file_url=file_url
        )
        
        # Update status based on result
        try:
            if result and result.get('success'):
                success = await file_repo.update_file(
                    file_id=file_id,
                    processing_status='completed',
                    updated_at=datetime.utcnow()
                )
                if success:
                    logger.info(f"Successfully processed file {file_id}")
                else:
                    logger.error(f"Failed to update file {file_id} status to 'completed'")
            else:
                error = result.get('error', 'Unknown error') if result else 'No result returned'
                success = await file_repo.update_file(
                    file_id=file_id,
                    processing_status='failed',
                    error_message=error[:500],
                    updated_at=datetime.utcnow()
                )
                if success:
                    logger.error(f"Failed to process file {file_id}: {error}")
                else:
                    logger.error(f"Failed to update file {file_id} status to 'failed': {error}")
            
            # Commit the transaction
            await repo_manager.commit()
                
        except Exception as e:
            error_msg = f"Unexpected error processing file {file_id}: {str(e)}"
            logger.exception(error_msg)
            
            # Try to update status even if processing failed
            if repo_manager is not None:
                try:
                    file_repo = repo_manager.file_repo
                    await file_repo.update_file(
                        file_id=file_id,
                        processing_status='failed',
                        error_message=error_msg[:500],
                        updated_at=datetime.utcnow()
                    )
                    await repo_manager.commit()
                except Exception as update_err:
                    logger.error(f"Failed to update file status after error: {str(update_err)}")
                    if hasattr(repo_manager, 'rollback'):
                        await repo_manager.rollback()
    
    except asyncio.CancelledError:
        logger.warning(f"Processing cancelled for file {file_id}")
        error_msg = "Processing was cancelled"
        if repo_manager is not None:
            try:
                file_repo = repo_manager.file_repo
                await file_repo.update_file(
                    file_id=file_id,
                    processing_status='failed',
                    error_message=error_msg,
                    updated_at=datetime.utcnow()
                )
                await repo_manager.commit()
            except Exception as update_err:
                logger.error(f"Failed to update file status after cancellation: {str(update_err)}")
                if hasattr(repo_manager, 'rollback'):
                    await repo_manager.rollback()
        raise
        
    except Exception as e:
        error_msg = f"Error processing file {file_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if repo_manager is not None:
            try:
                file_repo = repo_manager.file_repo
                await file_repo.update_file(
                    file_id=file_id,
                    status='failed',
                    error_message=error_msg[:500],
                    updated_at=datetime.utcnow()
                )
                await repo_manager.commit()
            except Exception as update_err:
                logger.error(f"Failed to update file status after error: {str(update_err)}")
                if hasattr(repo_manager, 'rollback'):
                    await repo_manager.rollback()
                    
    finally:
        # Clean up any temporary resources
        if 'temp_file' in locals() and temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
                logger.debug(f"Cleaned up temporary file: {temp_file}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file}: {e}")
        
        # Ensure semaphore is always released
        if semaphore.locked():
            semaphore.release()
            logger.debug("Released semaphore after processing")
            
        # Ensure repository manager is properly closed
        if repo_manager is not None:
            try:
                # Close the repository manager which will handle session cleanup
                if hasattr(repo_manager, 'close'):
                    await repo_manager.close()
                    logger.debug("Successfully closed repository manager")
            except Exception as close_error:
                logger.error(f"Error during repository manager cleanup: {str(close_error)}", exc_info=True)
        
        logger.info(f"Completed processing for file {file_id}")


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
    
    logger.info("Starting worker loop")
    
    # Initialize repository manager once at the start
    repo_manager = get_repository_manager()
    
    while not shutdown_event.is_set():
        try:
            # Clean up completed tasks
            running_tasks[:] = [t for t in running_tasks if not t.done()]
            
            # Check if we've reached max concurrent tasks
            if len(running_tasks) >= MAX_CONCURRENT_TASKS:
                logger.debug(f"Reached max concurrent tasks ({MAX_CONCURRENT_TASKS}), waiting...")
                await asyncio.sleep(1)
                continue
                
            # Calculate how many more tasks we can start
            available_slots = MAX_CONCURRENT_TASKS - len(running_tasks)
            
            # Get a new repository manager for this iteration
            repo_manager = get_repository_manager()
            
            try:
                # Fetch pending files with a limit based on available slots
                pending_files = await fetch_pending_files()
                
                if pending_files:
                    logger.info(f"Found {len(pending_files)} files to process (available slots: {available_slots})")
                    
                    # Reset poll interval since we found work
                    if current_poll_interval > INITIAL_POLL_INTERVAL:
                        logger.info(f"Resetting poll interval from {current_poll_interval}s to {INITIAL_POLL_INTERVAL}s")
                        current_poll_interval = INITIAL_POLL_INTERVAL
                    
                    # Process files up to available slots
                    for file in pending_files[:available_slots]:
                        try:
                            # Create and track the task
                            task = asyncio.create_task(process_single_file(file))
                            
                            # Add the task to running tasks first
                            running_tasks.append(task)
                            
                            # Create a cleanup callback that will be called when the task completes
                            def create_cleanup(task_to_clean):
                                async def cleanup(_):
                                    try:
                                        if task_to_clean in running_tasks:
                                            running_tasks.remove(task_to_clean)
                                        # Ensure task is properly closed
                                        if not task_to_clean.done() and not task_to_clean.cancelled():
                                            task_to_clean.cancel()
                                    except Exception as e:
                                        logger.warning(f"Error in task cleanup: {e}")
                                return cleanup
                            
                            # Register the cleanup callback
                            cleanup = create_cleanup(task)
                            task.add_done_callback(lambda t, c=cleanup: asyncio.create_task(c(t)))
                            
                            # Small delay between starting tasks to avoid thundering herd
                            await asyncio.sleep(0.1)
                            
                        except Exception as e:
                            file_id = file.get('id', 'unknown')
                            logger.error(f"Error creating task for file {file_id}: {e}", exc_info=True)
                            # Use the repo manager to update status
                            try:
                                file_repo = repo_manager.file_repo
                                await file_repo.update_file(
                                    file_id=file_id,
                                    processing_status='failed',
                                    error_message=f"Failed to start processing: {str(e)[:500]}",
                                    updated_at=datetime.utcnow()
                                )
                                await repo_manager.commit()
                            except Exception as update_err:
                                logger.error(f"Failed to update file {file_id} status: {str(update_err)}", exc_info=True)
                                if hasattr(repo_manager, 'rollback'):
                                    await repo_manager.rollback()
                
                else:
                    # No pending files, use exponential backoff
                    logger.debug(f"No pending files found. Next poll in {current_poll_interval:.1f} seconds")
                    
                    # Calculate next poll interval with exponential backoff
                    next_interval = min(
                        current_poll_interval * POLL_BACKOFF_FACTOR,
                        MAX_POLL_INTERVAL
                    )
                    
                    # Only log when the interval changes significantly
                    if next_interval > current_poll_interval * 1.5:  # Only log significant increases
                        logger.info(f"No work found, increasing poll interval to {next_interval:.1f} seconds")
                    
                    current_poll_interval = next_interval
                    
                    # Wait before polling again with the current interval
                    try:
                        # Split sleep into smaller chunks to be more responsive to shutdown
                        for _ in range(int(current_poll_interval)):
                            if shutdown_event.is_set():
                                break
                            await asyncio.sleep(1)
                        
                    except asyncio.CancelledError:
                        logger.info("Worker loop cancelled during sleep")
                        raise
            
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                raise
                
            except Exception as e:
                logger.exception(f"Unexpected error in worker loop: {str(e)}")
                # Use current poll interval before retrying, with jitter
                sleep_time = min(5 + (random.random() * 5), current_poll_interval)
                logger.info(f"Retrying after error in {sleep_time:.1f} seconds...")
                await asyncio.sleep(sleep_time)
                
            finally:
                # Close the repository manager for this iteration
                if repo_manager is not None:
                    await repo_manager.close()
    
        except asyncio.CancelledError:
            logger.info("Worker loop cancelled")
            raise
            
        except Exception as e:
            logger.exception(f"Unexpected error in worker loop: {str(e)}")
            # Wait before retrying
            await asyncio.sleep(5)
    
    # Clean up any remaining tasks during shutdown
    logger.info("Shutting down worker loop, cleaning up tasks...")
    
    # Cancel all running tasks
    for task in running_tasks:
        if not task.done() and not task.cancelled():
            task.cancel()
    
    # Wait for tasks to complete or be cancelled
    if running_tasks:
        logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")
        _, pending = await asyncio.wait(
            running_tasks,
            timeout=10.0,
            return_when=asyncio.ALL_COMPLETED
        )
        
        if pending:
            logger.warning(f"{len(pending)} tasks did not complete in time")
    
    logger.info("Worker loop shutdown complete")


async def handle_shutdown(sig, frame=None):
    """Handle shutdown signals gracefully"""
    global shutdown_event
    
    # Only process shutdown once
    if shutdown_event.is_set():
        return
        
    signal_name = signal.Signals(sig).name if hasattr(signal, 'Signals') else str(sig)
    logger.info(f"Received shutdown signal: {signal_name}")
    
    # Set the shutdown event to stop creating new tasks
    shutdown_event.set()
    
    # Log the number of running tasks
    running_count = len([t for t in running_tasks if not t.done()])
    if running_count > 0:
        logger.info(f"Waiting for {running_count} tasks to complete...")
        logger.info("No active tasks, shutting down immediately")


async def main():
    """
    Main entry point for the worker.
    
    Handles:
    - Signal registration for graceful shutdown
    - Worker task management
    - Cleanup of resources
    - Error handling and logging
    """
    logger.info(f"Starting SynText AI Worker (Max Concurrent Tasks: {MAX_CONCURRENT_TASKS})")
    
    # Initialize repository manager
    repo_manager = get_repository_manager()
    
    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_shutdown(s, None)))
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(f"Could not add signal handler for {sig}: {e}")
    
    worker_task = None
    try:
        # Start the worker loop as a task
        worker_task = asyncio.create_task(worker_loop())
        logger.info("Worker loop started successfully")
        
        # Wait for the worker task to complete (runs until shutdown)
        await worker_task
        
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
        raise  # Re-raise to ensure proper cleanup
        
    except Exception as e:
        logger.critical(f"Fatal error in worker: {str(e)}", exc_info=True)
        # Don't re-raise to allow for graceful shutdown
        
    finally:
        logger.info("Initiating worker shutdown...")
        
        # Set shutdown event to stop any running tasks
        shutdown_event.set()
        
        # Cancel the worker task if it's still running
        if worker_task and not worker_task.done():
            logger.debug("Cancelling worker task...")
            worker_task.cancel()
            try:
                await asyncio.wait_for(worker_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("Worker task did not complete within timeout")
        
        # Wait for remaining tasks to complete on shutdown
        if running_tasks:
            running_count = len(running_tasks)
            logger.info(f"Waiting for {running_count} remaining tasks to complete...")
            
            # Wait for tasks to complete with a timeout
            done, pending = await asyncio.wait(
                running_tasks,
                timeout=30.0,  # Max 30 seconds for graceful shutdown
                return_when=asyncio.ALL_COMPLETED
            )
            
            # Log any pending tasks that didn't complete
            if pending:
                logger.warning(f"{len(pending)} tasks did not complete during shutdown")
        
        # Cancel any remaining running tasks
        logger.info("Cancelling all remaining tasks...")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        
        # Wait for tasks to complete cancellation
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        # Close repository manager if it exists
        if 'repo_manager' in locals() and repo_manager is not None:
            try:
                if hasattr(repo_manager, 'close'):
                    await repo_manager.close()
                    logger.debug("Successfully closed repository manager")
            except Exception as e:
                logger.error(f"Error closing repository manager: {e}", exc_info=True)
            
        logger.info("SynText AI Worker shutdown complete")


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
