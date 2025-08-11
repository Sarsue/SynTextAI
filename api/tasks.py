"""
Background tasks for SynTextAI processing pipeline.

This module handles the orchestration of background tasks for processing user uploads,
including file processing, transcription, and content analysis. It serves as the main
entry point for all asynchronous operations in the application.

Key Responsibilities:
- File processing orchestration
- Status updates and error handling
- Integration with various processing services
- WebSocket notifications

Note: This module should only contain high-level orchestration logic. All business
logic should be delegated to specialized services.
"""

from __future__ import annotations

# Standard library imports
import asyncio
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

# Third-party imports
import stripe
from fastapi import HTTPException

# Local imports
from .core.config import settings
from .repositories.repository_manager import RepositoryManager
from .services.agent_service import agent_service
from .services.repository_service import RepositoryService
from .websocket_manager import websocket_manager as websocket_service
from .models.transcription import (
    TranscriptSegment,
    TranscriptionInfo,
)
from .utils import delete_from_gcs
# Configure logging
logger = logging.getLogger(__name__)

# Initialize repository manager with settings from config
store = RepositoryManager(database_url=settings.DATABASE_URL)

# Initialize Stripe if API key is available
if settings.STRIPE_SECRET:
    stripe.api_key = settings.STRIPE_SECRET

# Models have been moved to models/transcription.py

async def transcribe_audio_chunked(
    file_path: str,
    language: str = "en",
    chunk_duration_ms: int = 30000,
    overlap_ms: int = 5000,
    timeout_seconds: int = 300,
) -> tuple[List[TranscriptSegment], TranscriptionInfo]:
    """Transcribe audio using the MCP service with enhanced error handling and type safety.
    
    This function handles the entire transcription pipeline including input validation,
    service communication, and result processing. It's designed to be robust against
    various failure modes and provides detailed error information.
    
    Args:
        file_path: Path to the audio file to transcribe. Must be a valid file path
            pointing to an accessible audio file.
        language: ISO 639-1 language code (e.g., 'en' for English). If None or empty,
            the service will attempt to auto-detect the language.
        chunk_duration_ms: Duration of each audio chunk in milliseconds (for future use).
        overlap_ms: Overlap between chunks in milliseconds (for future use).
        timeout_seconds: Maximum time to wait for the transcription to complete.
            
    Returns:
        A tuple containing:
            - List of transcript segments with word-level timestamps
            - Dictionary with transcription metadata and language information
            
    Raises:
        FileNotFoundError: If the input file doesn't exist or is not accessible
        ValueError: If the language code is invalid or the audio file is empty/corrupt
        asyncio.TimeoutError: If the transcription takes longer than timeout_seconds
        Exception: For any errors during the MCP service call or result processing
    """
    start_time = time.time()
    file_size = 0
    
    def log_duration() -> str:
        """Helper to log the duration of the transcription process."""
        duration = time.time() - start_time
        return f"(took {duration:.2f}s)"
    
    try:
        # Input validation
        if not os.path.exists(file_path):
            error_msg = f"Audio file not found: {file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
            
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            error_msg = f"Audio file is empty: {file_path}"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        if language and (not isinstance(language, str) or len(language.strip()) != 2):
            error_msg = "Language must be a valid ISO 639-1 language code (e.g., 'en', 'es')"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info(
            "Starting transcription of %s (size: %.2fMB) with language: %s",
            file_path,
            file_size / (1024 * 1024),
            language
        )
        
        # Import agent service here to avoid circular imports
        from .services.agent_service import agent_service
        
        # Prepare input for transcription service
        input_data: Dict[str, Any] = {
            'audio_file': file_path,
            'language': language.lower() if language else None,
            'beam_size': 5,
            'vad_filter': True,
            'vad_parameters': {
                'min_silence_duration_ms': 500,
                'speech_pad_ms': 200,
                'threshold': 0.5
            },
            'suppress_tokens': [-1],  # Suppress common filler words
            'word_timestamps': True,
            'max_audio_length': 60 * 30  # 30 minutes max by default
        }
        
        logger.debug("Calling transcription service with input: %s", input_data)
        
        # Call transcription service with timeout
        try:
            result = await asyncio.wait_for(
                agent_service.process("speech_to_text", input_data),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError as te:
            error_msg = f"Transcription timed out after {timeout_seconds} seconds"
            logger.error("%s %s", error_msg, log_duration())
            raise asyncio.TimeoutError(error_msg) from te
            
        if not result or not isinstance(result, dict):
            error_msg = f"Invalid response format from transcription service: {result}"
            logger.error("%s %s", error_msg, log_duration())
            raise ValueError(error_msg)
        
        # Extract and validate segments and info
        segments: List[Dict[str, Any]] = result.get('segments', [])
        info: Dict[str, Any] = result.get('info', {})
        
        if not segments:
            logger.warning("No transcription segments returned for %s", file_path)
        else:
            logger.debug("Received %d segments from transcription service", len(segments))
        
        # Convert segments to the expected format with validation
        processed_segments = adapt_whisper_segments_to_transcript_data(segments)
        
        # Prepare response with type-safe structure
        response_info: TranscriptionInfo = {
            'language': info.get('language', language or 'unknown'),
            'language_probability': float(info.get('language_probability', 0.0)),
            'duration': float(info.get('duration', 0.0)),
            'all_language_probs': info.get('all_language_probs'),
            'error': None,
            'processing_time': time.time() - start_time,
            'file_size': file_size,
            'segment_count': len(processed_segments)
        }
        
        # Log success with detailed information
        logger.info(
            "Transcription completed for %s %s. Language: %s (confidence: %.2f), "
            "Duration: %.2fs, Segments: %d",
            os.path.basename(file_path),
            log_duration(),
            response_info['language'],
            response_info['language_probability'],
            response_info['duration'],
            len(processed_segments)
        )
        
        return processed_segments, response_info
        
    except asyncio.CancelledError:
        logger.warning("Transcription of %s was cancelled %s", file_path, log_duration())
        raise
        
    except Exception as e:
        error_msg = f"Error during transcription of {file_path}: {str(e)}"
        logger.error("%s %s", error_msg, log_duration(), exc_info=True)
        
        # Return minimal error information with type safety
        error_info: TranscriptionInfo = {
            'language': language or 'unknown',
            'language_probability': 0.0,
            'duration': 0.0,
            'all_language_probs': None,
            'error': error_msg,
            'processing_time': time.time() - start_time,
            'file_size': file_size,
            'segment_count': 0
        }
        return [], error_info

def adapt_whisper_segments_to_transcript_data(whisper_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts Whisper segments to the format expected by downstream processing.
    
    This function takes the raw segments from the Whisper transcription service and
    transforms them into a standardized format with proper typing and validation.
    
    Args:
        whisper_segments: List of segment dictionaries from the transcription service.
            Each segment should contain at least 'start', 'end', and 'text' keys,
            and optionally a 'words' list with word-level timing information.
            
    Returns:
        List of segments in the standardized format with the following structure:
        [
            {
                'start': float,       # Start time in seconds
                'end': float,         # End time in seconds
                'text': str,          # Transcribed text (stripped of whitespace)
                'words': [            # Optional list of word-level details
                    {
                        'word': str,          # The word text
                        'start': float,       # Word start time in seconds
                        'end': float,         # Word end time in seconds
                        'probability': float  # Confidence score (0.0 to 1.0)
                    },
                    ...
                ]
            },
            ...
        ]
        
    Raises:
        TypeError: If whisper_segments is not a list or contains invalid items
        ValueError: If required fields are missing or have invalid types
    """
    if not isinstance(whisper_segments, list):
        raise TypeError(f"Expected list of segments, got {type(whisper_segments).__name__}")
    
    if not whisper_segments:
        return []
    
    result = []
    
    for i, segment in enumerate(whisper_segments, 1):
        if not isinstance(segment, dict):
            logger.warning("Skipping invalid segment at index %d: not a dictionary", i)
            continue
            
        try:
            # Extract and validate segment data
            segment_data = {
                'start': float(segment.get('start', 0.0)),
                'end': float(segment.get('end', 0.0)),
                'text': str(segment.get('text', '')).strip(),
                'words': []
            }
            
            # Process word-level data if available
            if 'words' in segment and isinstance(segment['words'], list):
                for word_idx, word in enumerate(segment['words'], 1):
                    if not isinstance(word, dict):
                        logger.warning(
                            "Skipping invalid word at index %d in segment %d: not a dictionary",
                            word_idx, i
                        )
                        continue
                        
                    try:
                        word_data = {
                            'word': str(word.get('word', '')),
                            'start': float(word.get('start', 0.0)),
                            'end': float(word.get('end', 0.0)),
                            'probability': float(word.get('probability', 1.0))
                        }
                        segment_data['words'].append(word_data)
                    except (TypeError, ValueError) as e:
                        logger.warning(
                            "Skipping invalid word data at index %d in segment %d: %s",
                            word_idx, i, str(e)
                        )
            
            result.append(segment_data)
            
        except (TypeError, ValueError) as e:
            logger.warning("Skipping invalid segment at index %d: %s", i, str(e))
            continue
    
    if not result and whisper_segments:
        logger.warning("No valid segments found in the provided input")
    
    return result

async def process_file_data(
    user_gc_id: str, 
    user_id: str, 
    file_id: str, 
    filename: str, 
    file_url: str, 
    is_youtube: bool = False, 
    language: str = "en", 
    comprehension_level: str = "beginner",
    firebase_token: Optional[str] = None
):
    """Process an uploaded file using the MCP service.
    
    Handles both document uploads and YouTube videos by:
    1. Determining file type
    2. Extracting/transcribing content
    3. Generating key concepts
    4. Creating study materials (flashcards, quizzes)
    5. Updating the database with results
    
    Args:
        user_gc_id: Google Cloud user ID
        user_id: Application user ID
        file_id: ID of the file in the database
        filename: Original filename or YouTube URL
        file_url: URL to download the file (if not YouTube)
        is_youtube: Whether this is a YouTube video
        language: Language code for processing (e.g., 'en', 'es')
        comprehension_level: User's comprehension level (beginner, intermediate, advanced)
        firebase_token: Firebase authentication token for background processing
    """
    logger.info(f"========== STARTING PROCESSING FOR FILE: {filename} (ID: {file_id}) ===========")
    logger.info(f"Processing details - User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level}")
    
    # Initialize services
    repository_service = RepositoryService(store)
    
    # Helper function to send WebSocket updates
    async def send_ws_update(event_type: str, data: Dict[str, Any] = None, progress: float = None):
        """Send a WebSocket update to the client."""
        if data is None:
            data = {}
            
        # Include file_id in all updates
        data['file_id'] = file_id
        
        # Add progress if provided
        if progress is not None:
            data['progress'] = progress
            
        try:
            await websocket_service.send_message(
                user_id=str(user_id),
                event_type=event_type,
                data=data
            )
            logger.debug(f"Sent WebSocket update: {event_type}")
        except Exception as e:
            logger.error(f"Error sending WebSocket update: {str(e)}", exc_info=True)
    
    # Helper function to update file status with retry logic and WebSocket updates
    async def update_status(status: str, error_msg: str = None, progress: float = None):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                from api.worker import update_file_status
                success = await update_file_status(
                    int(file_id), 
                    status,
                    error=error_msg
                )
                if success:
                    logger.info(f"Updated file {file_id} status to {status}")
                    # Send WebSocket update
                    await send_ws_update(
                        event_type="file_status_update",
                        data={
                            "status": status,
                            "error_message": error_msg,
                            "progress": progress
                        }
                    )
                    return True
                else:
                    logger.warning(f"Failed to update status (attempt {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.error(f"Error updating status (attempt {attempt + 1}/{max_retries}): {str(e)}", exc_info=True)
                
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                
        logger.error(f"Failed to update file {file_id} status to {status} after {max_retries} attempts")
        
        # Send error update via WebSocket
        try:
            await send_ws_update(
                event_type="file_status_error",
                data={
                    "status": "error",
                    "error_message": f"Failed to update status after {max_retries} attempts",
                    "progress": progress
                }
            )
        except Exception as e:
            logger.error(f"Error sending error WebSocket update: {str(e)}", exc_info=True)
            
        return False
    
    # Get file object with error handling
    try:
        # Send initial processing started update
        await send_ws_update(
            event_type="file_processing_started",
            data={
                "filename": filename,
                "is_youtube": is_youtube,
                "language": language,
                "comprehension_level": comprehension_level
            },
            progress=0
        )
        
        file = store.file_repo.get_file_by_id(file_id)
        if not file:
            error_msg = f"File not found with ID: {file_id}"
            logger.error(error_msg)
            await send_ws_update(
                event_type="file_processing_error",
                data={"error": error_msg},
                progress=0
            )
            return False
            
        # Helper to safely get/set file attributes
        def get_attr(file_obj, attr, default=None):
            if hasattr(file_obj, attr):
                return getattr(file_obj, attr)
            elif isinstance(file_obj, dict) and attr in file_obj:
                return file_obj[attr]
            return default
            
        def set_attr(file_obj, attr, value):
            if hasattr(file_obj, attr):
                setattr(file_obj, attr, value)
            elif isinstance(file_obj, dict):
                file_obj[attr] = value
                
        # Determine and set file type if not already set
        file_type = get_attr(file, 'file_type')
        if not file_type:
            if is_youtube or "youtube.com" in filename or "youtu.be" in filename:
                file_type = "youtube"
            elif filename.lower().endswith(".pdf"):
                file_type = "pdf"
            elif filename.lower().endswith((".docx", ".doc")):
                file_type = "docx"
            else:
                file_type = os.path.splitext(filename)[1][1:].lower() or "unknown"
                
            set_attr(file, 'file_type', file_type)
            try:
                if hasattr(file, 'session'):
                    file.session.commit()
                elif hasattr(store.file_repo, 'session'):
                    store.file_repo.session.commit()
            except Exception as e:
                logger.error(f"Failed to commit file type update: {str(e)}")
        
        # Check current processing status
        processing_status = get_attr(file, 'processing_status')
        if processing_status in ["processing", "extracting"]:
            logger.warning(f"File {file_id} is already being processed (status: {processing_status}).")
            return True
        elif processing_status == "processed":
            logger.info(f"File {file_id} is already processed.")
            return True
            
        # Update status to EXTRACTING
        if not await update_status("extracting", progress=0.1):
            logger.error(f"Failed to update status to extracting for file {file_id}")
            return False
            
        # Create a temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = None
            content = None
            metadata = {}
            
            # Notify client that we're starting file download/extraction
            await send_ws_update(
                event_type="file_download_started",
                data={
                    "file_type": file_type,
                    "is_youtube": is_youtube
                },
                progress=0.1
            )
            
            # Process based on file type
            if file_type == "youtube":
                # Extract video ID from URL
                video_id = None
                if "youtube.com/watch?v=" in filename:
                    video_id = filename.split("v=")[1].split("&")[0]
                elif "youtu.be/" in filename:
                    video_id = filename.split("youtu.be/")[1].split("?")[0]
                
                if not video_id:
                    raise ValueError("Could not extract video ID from YouTube URL")
                
                # Process YouTube video
                await update_status("downloading", progress=0.2)
                await send_ws_update(
                    event_type="youtube_download_started",
                    data={
                        "video_id": video_id,
                        "status": "downloading"
                    },
                    progress=0.2
                )
                
                try:
                    result = await agent_service.process_content(
                        agent_name="ingestion",
                        content=video_id,
                        content_type="youtube",
                        language=language
                    )
                    
                    if result.get('status') != 'success':
                        error_msg = f"Failed to process YouTube video: {result.get('error', 'Unknown error')}"
                        await send_ws_update(
                            event_type="youtube_download_error",
                            data={
                                "video_id": video_id,
                                "error": error_msg
                            },
                            progress=0.2
                        )
                        raise Exception(error_msg)
                    
                    # Notify about successful download and start of transcription
                    await send_ws_update(
                        event_type="youtube_download_completed",
                        data={
                            "video_id": video_id,
                            "duration": result.get('transcription', {}).get('duration')
                        },
                        progress=0.4
                    )
                    
                    # Extract content and metadata
                    segments = result.get('transcription', {}).get('segments', [])
                    content = "\n".join([seg['text'] for seg in segments])
                    metadata = {
                        'type': 'youtube',
                        'video_id': video_id,
                        'duration': result.get('transcription', {}).get('duration'),
                        'language': result.get('transcription', {}).get('language'),
                        'segment_count': len(segments)
                    }
                    
                    # Notify about transcription completion
                    await send_ws_update(
                        event_type="youtube_transcription_completed",
                        data={
                            "video_id": video_id,
                            "segment_count": len(segments),
                            "language": metadata['language']
                        },
                        progress=0.6
                    )
                    
                except Exception as e:
                    logger.error(f"Error processing YouTube video {video_id}: {str(e)}", exc_info=True)
                    await send_ws_update(
                        event_type="youtube_processing_error",
                        data={
                            "video_id": video_id,
                            "error": str(e)
                        },
                        progress=0.2
                    )
                    raise
                
            else:
                # Download document
                await update_status("downloading", progress=0.2)
                file_path = os.path.join(temp_dir, filename)
                
                # Notify about document download start
                await send_ws_update(
                    event_type="document_download_started",
                    data={
                        "filename": filename,
                        "file_type": file_type
                    },
                    progress=0.2
                )
                
                try:
                    # Download file from GCS using the existing utility
                    from utils import download_from_gcs
                    
                    # Extract the filename from the GCS URL
                    gcs_filename = file_url.split('/')[-1]
                    
                    # Download the file
                    file_data = download_from_gcs(user_gc_id, gcs_filename)
                    if not file_data:
                        error_msg = "Failed to download file from GCS"
                        await send_ws_update(
                            event_type="document_download_error",
                            data={
                                "filename": filename,
                                "error": error_msg
                            },
                            progress=0.2
                        )
                        raise Exception(error_msg)
                    
                    # Save the file locally
                    with open(file_path, 'wb') as f:
                        f.write(file_data)
                    
                    # Update progress
                    await update_status("downloading", progress=0.4)
                    
                    # Notify about successful download
                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    await send_ws_update(
                        event_type="document_download_completed",
                        data={
                            "filename": filename,
                            "file_size_mb": round(file_size_mb, 2)
                        },
                        progress=0.4
                    )
                    
                except Exception as e:
                    logger.error(f"Error downloading document {filename}: {str(e)}", exc_info=True)
                    await send_ws_update(
                        event_type="document_processing_error",
                        data={
                            "filename": filename,
                            "error": str(e)
                        },
                        progress=0.2
                    )
                    raise
                
                # Process document based on file type
                try:
                    # Notify about document processing start
                    await send_ws_update(
                        event_type="document_processing_started",
                        data={
                            "filename": filename,
                            "file_type": file_type,
                            "status": "processing"
                        },
                        progress=0.4
                    )
                    
                    result = await agent_service.process_content(
                        agent_name="ingestion",
                        content=file_path,
                        content_type=file_type,
                        language=language
                    )
                    
                    if result.get('status') != 'success':
                        error_msg = f"Failed to process document: {result.get('error', 'Unknown error')}"
                        await send_ws_update(
                            event_type="document_processing_error",
                            data={
                                "filename": filename,
                                "error": error_msg
                            },
                            progress=0.4
                        )
                        raise Exception(error_msg)
                    
                    content = result.get('text', '')
                    metadata = {
                        'type': file_type,
                        'file_size': result.get('file_size'),
                        'pages': result.get('pages', 1),
                        'language': language,
                        'word_count': len(content.split())
                    }
                    
                    # Notify about successful document processing
                    await send_ws_update(
                        event_type="document_processing_completed",
                        data={
                            "filename": filename,
                            "pages": metadata['pages'],
                            "word_count": metadata['word_count'],
                            "language": language
                        },
                        progress=0.6
                    )
                    
                except Exception as e:
                    logger.error(f"Error processing document {filename}: {str(e)}", exc_info=True)
                    await send_ws_update(
                        event_type="document_processing_error",
                        data={
                            "filename": filename,
                            "error": str(e)
                        },
                        progress=0.4
                    )
                    raise
                
                # Update status to PROCESSING
                logger.info("Updating status to PROCESSING")
                success = await update_status("processing", progress=0.5)
                if not success:
                    logger.error(f"Failed to update status to processing for file {file_id}")
                    return
                
                # Generate key concepts
                logger.info("Extracting key concepts...")
                try:
                    # Notify about concept extraction start
                    await send_ws_update(
                        event_type="concept_extraction_started",
                        data={
                            "filename": filename,
                            "status": "extracting_concepts"
                        },
                        progress=0.6
                    )
                    
                    concepts_result = await agent_service.process_content(
                        agent_name="summarization",
                        content=content,
                        content_type="text",
                        language=language,
                        comprehension_level=comprehension_level,
                        task="extract_concepts"
                    )
                    
                    if concepts_result.get('status') != 'success':
                        error_msg = f"Failed to extract concepts: {concepts_result.get('error', 'Unknown error')}"
                        await send_ws_update(
                            event_type="concept_extraction_error",
                            data={
                                "filename": filename,
                                "error": error_msg
                            },
                            progress=0.6
                        )
                        raise Exception(error_msg)
                    
                    concepts = concepts_result.get('concepts', [])
                    
                    # Notify about successful concept extraction
                    await send_ws_update(
                        event_type="concept_extraction_completed",
                        data={
                            "filename": filename,
                            "concept_count": len(concepts)
                        },
                        progress=0.7
                    )
                    
                    # Generate flashcards and quizzes from concepts
                    logger.info("Generating study materials...")
                    await send_ws_update(
                        event_type="study_materials_generation_started",
                        data={
                            "filename": filename,
                            "status": "generating_study_materials"
                        },
                        progress=0.75
                    )
                    
                    # Generate flashcards
                    flashcard_result = await agent_service.process_content(
                        agent_name="quiz",
                        content=content,
                        content_type="concepts",
                        language=language,
                        comprehension_level=comprehension_level,
                        concepts=concepts,
                        num_items=5,
                        item_type="flashcards"
                    )
                    
                    if flashcard_result.get('status') != 'success':
                        error_msg = f"Failed to generate flashcards: {flashcard_result.get('error', 'Unknown error')}"
                        await send_ws_update(
                            event_type="study_materials_error",
                            data={
                                "filename": filename,
                                "error": error_msg,
                                "material_type": "flashcards"
                            },
                            progress=0.75
                        )
                        raise Exception(error_msg)
                    
                    # Generate quizzes
                    try:
                        # Notify about quiz generation start
                        await send_ws_update(
                            event_type="quiz_generation_started",
                            data={
                                "filename": filename,
                                "status": "generating_quizzes"
                            },
                            progress=0.8
                        )
                        
                        quiz_result = await agent_service.process_content(
                            agent_name="quiz",
                            content=content,
                            content_type="concepts",
                            language=language,
                            comprehension_level=comprehension_level,
                            concepts=concepts,
                            num_items=5,
                            item_type="quizzes"
                        )
                        
                        if quiz_result.get('status') != 'success':
                            error_msg = f"Failed to generate quizzes: {quiz_result.get('error', 'Unknown error')}"
                            await send_ws_update(
                                event_type="study_materials_error",
                                data={
                                    "filename": filename,
                                    "error": error_msg,
                                    "material_type": "quizzes"
                                },
                                progress=0.8
                            )
                            raise Exception(error_msg)
                        
                        # Prepare study results
                        study_results = {
                            'flashcards': flashcard_result.get('flashcards', []),
                            'quizzes': quiz_result.get('quizzes', [])
                        }
                        
                        # Notify about successful study materials generation
                        await send_ws_update(
                            event_type="study_materials_completed",
                            data={
                                "filename": filename,
                                "flashcard_count": len(study_results.get('flashcards', [])),
                                "quiz_count": len(study_results.get('quizzes', []))
                            },
                            progress=0.9
                        )
                        
                        # Save to database
                        await repository_service.save_processing_results(
                            file_id=file_id,
                            user_id=user_id,
                            concepts=concepts,
                            study_results=study_results,
                            metadata={
                                'language': language,
                                'comprehension_level': comprehension_level,
                                'processing_time': time.time() - start_time,
                                'is_youtube': is_youtube
                            }
                        )
                        
                        # Update status to COMPLETED
                        success = await update_status("completed", progress=1.0)
                        if not success:
                            error_msg = f"Failed to update status to completed for file {file_id}"
                            logger.error(error_msg)
                            raise Exception(error_msg)
                        
                        # Final success notification
                        await send_ws_update(
                            event_type="processing_completed",
                            data={
                                "filename": filename,
                                "status": "completed",
                                "concept_count": len(concepts),
                                "flashcard_count": len(study_results.get('flashcards', [])),
                                "quiz_count": len(study_results.get('quizzes', []))
                            },
                            progress=1.0
                        )
                        
                    except Exception as e:
                        logger.error(f"Error during quiz generation or final processing: {str(e)}", exc_info=True)
                        await send_ws_update(
                            event_type="processing_error",
                            data={
                                "filename": filename,
                                "error": str(e),
                                "stage": "quiz_generation"
                            },
                            progress=0.8
                        )
                        raise Exception(f"Failed to generate quizzes or complete processing: {str(e)}")
                        logger.error(f"Error saving processing results: {str(e)}", exc_info=True)
                        raise Exception(f"Failed to save processing results: {str(e)}")
                except Exception as e:
                    logger.error(f"Error generating study materials: {str(e)}", exc_info=True)
                    raise Exception(f"Failed to generate study materials: {str(e)}")
                
                # Update status to PROCESSED after successful processing
                logger.info("Updating status to PROCESSED")
                success = await update_status("processed", progress=1.0)
                if not success:
                    logger.error(f"Failed to update status to processed for file {file_id}")
                    return
                
                logger.info(f"Successfully processed file {file_id}")
                return
            
    except Exception as e:
        logger.error(f"Error in file processing: {e}", exc_info=True)
        # Update status to FAILED
        success = await update_status("failed", error_msg=str(e))
        if not success:
            logger.error(f"Failed to update status to failed for file {file_id}")
    
    # We've either had a fatal error or processing has completed
    return


async def delete_user_task(user_id: str, user_gc_id: str):
    """Deletes a user's account, subscription, and associated files."""
    try:
        user_sub = store.get_subscription(user_id)
        if user_sub and user_sub.get("status") == "active":
            stripe_sub_id = user_sub.get("stripe_subscription_id")
            stripe_customer_id = user_sub.get("stripe_customer_id")

            # Cancel the subscription
            if stripe_sub_id:
                stripe.Subscription.delete(stripe_sub_id)
                logger.info(f"Subscription {stripe_sub_id} canceled.")

            # Remove payment methods and delete the customer
            if stripe_customer_id:
                payment_methods = stripe.PaymentMethod.list(customer=stripe_customer_id, type="card")
                for method in payment_methods.auto_paging_iter():
                    stripe.PaymentMethod.detach(method.id)
                    logger.info(f"Payment method {method.id} detached.")
                
                stripe.Customer.delete(stripe_customer_id)
                logger.info(f"Stripe customer {stripe_customer_id} deleted.")

        # Delete files and user account
        for file in store.get_files_for_user(user_id):
            delete_from_gcs(user_gc_id, file["name"])

        store.delete_user_account(user_id)
        logger.info(f"User account {user_id} deleted.")

    except Exception as e:
        logger.error(f"Error during user deletion: {e}")
        raise HTTPException(status_code=500, detail="User deletion failed")
