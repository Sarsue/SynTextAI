import logging
import os
import tempfile
from contextlib import contextmanager
import asyncio
import yt_dlp
from typing import Optional, Dict, List, Tuple, Any, Union
import json

# Internal imports
from utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from repositories.repository_manager import RepositoryManager
from .services.agent_service import agent_service
import stripe
from websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import BackgroundTasks, HTTPException
import gc
from datetime import datetime
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# MCP service will handle model loading and management

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

LANGUAGE_CODE_MAP = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    # Add more as needed. The youtube_transcript_api might also accept full names.
}

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET')

# Initialize DocSynthStore and SyntextAgent

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
syntext = SyntextAgent()

# MCP service manages model lifecycle



from typing import TypedDict, List, Optional, Dict, Any
from pathlib import Path

class WordSegment(TypedDict):
    """Represents a single word in the transcription with timing information."""
    word: str
    start: float
    end: float
    probability: float

class TranscriptSegment(TypedDict):
    """Represents a segment of transcribed text with timing and word-level details."""
    start: float
    end: float
    text: str
    words: List[WordSegment]

class TranscriptionInfo(TypedDict, total=False):
    """Metadata about the transcription process and results."""
    language: str
    language_probability: float
    duration: float
    all_language_probs: Optional[Dict[str, float]]
    error: Optional[str]

async def transcribe_audio_chunked(
    file_path: str, 
    language: str = "en", 
    chunk_duration_ms: int = 30000, 
    overlap_ms: int = 5000,
    timeout_seconds: int = 300,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
    
    def log_duration():
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
            
        if language and (not isinstance(language, str) or len(language) != 2):
            error_msg = "Language must be a valid ISO 639-1 language code (e.g., 'en', 'es')"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info(f"Starting transcription of {file_path} (size: {file_size/1024/1024:.2f}MB) with language: {language}")
        
        # Prepare input for MCP service with enhanced parameters
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
        
        logger.debug(f"Calling MCP service with input: {input_data}")
        
        # Call MCP service for transcription with timeout
        try:
            result = await asyncio.wait_for(
                mcp_service.process("speech_to_text", input_data),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            error_msg = f"Transcription timed out after {timeout_seconds} seconds"
            logger.error(f"{error_msg} {log_duration()}")
            raise asyncio.TimeoutError(error_msg) from None
            
        if not result or not isinstance(result, dict):
            error_msg = f"Invalid response format from MCP service: {result}"
            logger.error(f"{error_msg} {log_duration()}")
            raise ValueError(error_msg)
        
        # Extract and validate segments and info
        segments: List[Dict[str, Any]] = result.get('segments', [])
        info: Dict[str, Any] = result.get('info', {})
        
        if not segments:
            logger.warning(f"No transcription segments returned for {file_path}")
        else:
            logger.debug(f"Received {len(segments)} segments from MCP service")
        
        # Convert segments to the expected format with validation
        processed_segments = adapt_whisper_segments_to_transcript_data(segments)
        
        # Prepare response with type-safe structure
        response_info = {
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
            f"Transcription completed for {os.path.basename(file_path)} {log_duration()}. "
            f"Language: {response_info['language']} (confidence: {response_info['language_probability']:.2f}), "
            f"Duration: {response_info['duration']:.2f}s, "
            f"Segments: {len(processed_segments)}"
        )
        
        return processed_segments, response_info
        
    except asyncio.CancelledError:
        logger.warning(f"Transcription of {file_path} was cancelled {log_duration()}")
        raise
        
    except Exception as e:
        error_msg = f"Error during transcription of {file_path}: {str(e)}"
        logger.error(f"{error_msg} {log_duration()}", exc_info=True)
        
        # Return minimal error information with type safety
        return [], {
            'language': language or 'unknown',
            'language_probability': 0.0,
            'duration': 0.0,
            'all_language_probs': None,
            'error': error_msg,
            'processing_time': time.time() - start_time,
            'file_size': file_size if 'file_size' in locals() else 0,
            'segment_count': 0
        }

def adapt_whisper_segments_to_transcript_data(whisper_segments: list) -> list:
    """Converts Whisper segments to the format expected by downstream processing.
    
    Args:
        whisper_segments: List of segment dictionaries from MCP service
        
    Returns:
        List of segments in the expected format
    """
    if not whisper_segments:
        return []
        
    return [
        {
            "start": segment.get("start", 0.0),
            "end": segment.get("end", 0.0),
            "text": segment.get("text", "").strip(),
            "words": [
                {
                    "word": word.get("word", ""),
                    "start": word.get("start", 0.0),
                    "end": word.get("end", 0.0),
                    "probability": word.get("probability", 1.0),
                }
                for word in segment.get("words", [])
            ],
        }
        for segment in whisper_segments
    ]

def download_youtube_audio_segment(video_id: str, language_code_for_whisper: Optional[str] = None) -> Optional[str]:
    """Downloads audio from a YouTube video, trying to get an audio track that matches the language if possible."""
    output_dir = tempfile.mkdtemp()
    output_template = os.path.join(output_dir, '%(id)s.%(ext)s')
    
    ydl_opts = {
        'format': 'bestaudio/best', # Prioritize best audio quality
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True, # Sometimes helps with SSL issues
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3', # Or 'wav', 'm4a'
            'preferredquality': '192', # Standard quality
        }],
    }

    # If a language code is provided, try to get audio for that language (if available as separate track)
    # This is more relevant for multi-language videos, often not the case.
    # if language_code_for_whisper:
    #     ydl_opts['format'] = f'bestaudio[language={language_code_for_whisper}]/bestaudio/best'

    logger.info(f"Attempting to download audio for YouTube video ID: {video_id} with options: {ydl_opts}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
            downloaded_file = ydl.prepare_filename(info_dict)
            # yt-dlp might add .webm or other extension before ffmpeg converts it.
            # The actual output file will be what ffmpeg creates (e.g., .mp3)
            base, _ = os.path.splitext(downloaded_file)
            expected_mp3_file = base + '.mp3'
            if os.path.exists(expected_mp3_file):
                logger.info(f"Audio successfully downloaded and converted to: {expected_mp3_file}")
                return expected_mp3_file
            else:
                # Fallback check if the original downloaded file (before potential conversion) exists
                # This might happen if ffmpeg postprocessor is not found or fails silently
                if os.path.exists(downloaded_file):
                    logger.warning(f"FFmpeg postprocessing to MP3 might have failed. Using original download: {downloaded_file}")
                    return downloaded_file
                logger.error(f"Audio download for {video_id} seemed to complete, but expected file {expected_mp3_file} not found.")
                return None
    except yt_dlp.utils.DownloadError as de:
        logger.error(f"yt-dlp DownloadError for video ID {video_id}: {de}")
        return None
    except Exception as e:
        logger.error(f"Error downloading YouTube audio for video ID {video_id}: {e}", exc_info=True)
        return None

async def process_file_data(user_gc_id: str, user_id: str, file_id: str, filename: str, file_url: str, is_youtube: bool = False, language: str = "en", comprehension_level: str = "beginner"):
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
    """
    logger.info(f"========== STARTING PROCESSING FOR FILE: {filename} (ID: {file_id}) ===========")
    logger.info(f"Processing details - User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level}")
    
    # Initialize MCP service
    from .services.agent_service import agent_service
    
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
            await websocket_manager.send_message(
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
                    error_message=error_msg,
                    progress=progress
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
                    # Download file from URL
                    async with aiohttp.ClientSession() as session:
                        async with session.get(file_url) as response:
                            if response.status != 200:
                                error_msg = f"Failed to download file: HTTP {response.status}"
                                await send_ws_update(
                                    event_type="document_download_error",
                                    data={
                                        "filename": filename,
                                        "error": error_msg
                                    },
                                    progress=0.2
                                )
                                raise Exception(error_msg)
                            
                            # Get file size for progress tracking
                            total_size = int(response.headers.get('content-length', 0))
                            downloaded_size = 0
                            
                            with open(file_path, 'wb') as f:
                                while True:
                                    chunk = await response.content.read(8192)
                                    if not chunk:
                                        break
                                    f.write(chunk)
                                    downloaded_size += len(chunk)
                                    
                                    # Update progress every 1MB or when done
                                    if downloaded_size % (1024 * 1024) < 8192 or downloaded_size == total_size:
                                        progress = 0.2 + (0.2 * (downloaded_size / max(total_size, 1)))
                                        await update_status("downloading", progress=progress)
                    
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
                        await send_ws_update(
                            event_type="saving_results",
                            data={
                                "filename": filename,
                                "status": "saving_to_database"
                            },
                            progress=0.95
                        )
                        
                        await save_processing_results(
                            store=store,
                            file_id=file_id,
                            user_id=user_id,
                            concepts=concepts,
                            study_results=study_results,
                            metadata=metadata
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

async def process_query_data(user_id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """
    Processes a user query and generates a response using QAAgent with RAG.
    
    Args:
        user_id: ID of the user making the query
        history_id: ID of the chat history (optional)
        message: User's query message
        language: Language code for the response
        comprehension_level: User's comprehension level
        
    Returns:
        Dict containing the response, sources, and history ID
    """
    logger.info(f"Processing query from user {user_id}: {message}")
    try:
        # Get repository manager
        store = get_repository_manager()
        
        # Get chat history if history_id is provided
        chat_history = []
        if history_id:
            chat_history = await store.chat_repo.get_chat_history(history_id)
        
        # Format history for context
        formatted_history = "\n".join([
            f"User: {msg.user_message}\nAssistant: {msg.assistant_message}" 
            for msg in chat_history
        ])
        
        # Process the query using the QA agent
        response = await agent_service.process_content(
            agent_name="qa",
            content=message,
            content_type="query",
            language=language,
            comprehension_level=comprehension_level,
            chat_history=formatted_history,
            user_id=user_id
        )
        
        # Save the interaction to chat history
        chat_message = {
            'user_id': user_id,
            'history_id': history_id or str(uuid.uuid4()),
            'user_message': message,
            'assistant_message': response.get('answer', 'I could not generate a response.'),
            'context': response.get('context', ''),
            'metadata': {
                'model': 'qa_agent',
                'language': language,
                'comprehension_level': comprehension_level,
                'sources': response.get('sources', [])
            }
        }
        
        await store.chat_repo.add_chat_message(chat_message)
        
        return {
            'response': response.get('answer', 'I could not generate a response.'),
            'sources': response.get('sources', []),
            'history_id': chat_message['history_id']
        }
            
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        await websocket_manager.send_message(id, "message_received", {"status": "error", "error": str(e)})
        raise HTTPException(status_code=500, detail="Query processing failed")

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





# New adapter functions to bridge the gap between the processor and the existing functions

async def save_processing_results(
    store: RepositoryManager,
    file_id: Union[str, int],
    user_id: Union[str, int],
    concepts: List[Dict[str, Any]],
    study_results: Dict[str, List[Dict[str, Any]]],
    metadata: Dict[str, Any]
) -> None:
    """
    Save processing results to the database using the repository pattern.
    
    Args:
        store: Repository manager instance
        file_id: ID of the file being processed
        user_id: ID of the user who owns the file
        concepts: List of concept dictionaries to save
        study_results: Dictionary containing flashcards and quizzes
        metadata: Additional metadata about the processing
    """
    try:
        # Convert IDs to integers if they're strings
        file_id = int(file_id)
        user_id = int(user_id)
        
        # Save concepts
        for concept in concepts:
            store.concept_repo.create_concept(
                file_id=file_id,
                user_id=user_id,
                title=concept.get('title', ''),
                explanation=concept.get('explanation', ''),
                metadata={
                    'source_page': concept.get('source_page'),
                    'start_time': concept.get('start_time'),
                    'end_time': concept.get('end_time'),
                    **metadata
                }
            )
        
        # Save flashcards
        for flashcard in study_results.get('flashcards', []):
            store.flashcard_repo.create_flashcard(
                file_id=file_id,
                user_id=user_id,
                front=flashcard.get('front', ''),
                back=flashcard.get('back', ''),
                metadata={
                    'concept': flashcard.get('concept'),
                    'difficulty': flashcard.get('difficulty'),
                    **metadata
                }
            )
        
        # Save quizzes
        for quiz in study_results.get('quizzes', []):
            store.quiz_repo.create_quiz(
                file_id=file_id,
                user_id=user_id,
                question=quiz.get('question', ''),
                options=quiz.get('options', []),
                correct_answer=quiz.get('correct_answer', ''),
                explanation=quiz.get('explanation', ''),
                quiz_type=quiz.get('type', 'multiple_choice'),
                metadata={
                    **metadata
                }
            )
        
        # Commit the transaction
        if hasattr(store, 'session'):
            store.session.commit()
        elif hasattr(store, 'file_repo') and hasattr(store.file_repo, 'session'):
            store.file_repo.session.commit()
            
        logger.info(f"Successfully saved processing results for file {file_id}")
        
    except Exception as e:
        logger.error(f"Error in save_processing_results: {str(e)}", exc_info=True)
        if hasattr(store, 'session'):
            store.session.rollback()
        elif hasattr(store, 'file_repo') and hasattr(store.file_repo, 'session'):
            store.file_repo.session.rollback()
        raise
