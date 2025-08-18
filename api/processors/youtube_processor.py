"""
YouTube processor module - Handles extraction and processing of YouTube videos.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple

# Import whisper only when needed to avoid import errors
try:
    import whisper
    import torch
except ImportError:
    whisper = None
    torch = None

from ..llm_compat import get_text_embeddings_in_batches, generate_key_concepts_dspy
from ..repositories.repository_manager import RepositoryManager
from .base_processor import FileProcessor
from .processor_utils import (
    generate_learning_materials_for_concept as utils_generate_learning_materials_for_concept,
    log_concept_processing_summary
)

logger = logging.getLogger(__name__)

# Global instance of RepositoryManager for the standalone function
_repo_manager: Optional[RepositoryManager] = None

async def process_youtube(
    video_url: str,
    file_id: int,
    user_id: int,
    **kwargs
) -> Dict[str, Any]:
    """
    Standalone function to process a YouTube video.
    
    This is a convenience function that creates a YouTubeProcessor instance and processes the video.
    
    Args:
        video_url: URL of the YouTube video
        file_id: ID of the file in the database
        user_id: ID of the user who owns the file
        **kwargs: Additional keyword arguments to pass to the processor
        
    Returns:
        Dictionary containing processing results
    """
    global _repo_manager
    if _repo_manager is None:
        from api.repositories.repository_manager import get_repository_manager
        _repo_manager = get_repository_manager()
        
    processor = YouTubeProcessor(_repo_manager)
    # Pass parameters in the order expected by processor.process:
    # user_id, file_id, filename, file_url, **kwargs
    return await processor.process(
        user_id=user_id,
        file_id=file_id,
        filename=video_url,  # Using video URL as filename
        file_url=video_url,  # Also pass as file_url
        **kwargs
    )

# Import required only for YouTube processing
try:
    # Try to import the transcript API
    from youtube_transcript_api import YouTubeTranscriptApi
    
    # Test if the required methods are available
    if not all(hasattr(YouTubeTranscriptApi, method) for method in ['list_transcripts', 'get_transcript']):
        logger.warning("YouTube Transcript API is missing required methods. YouTube processing will use Whisper fallback.")
        YouTubeTranscriptApi = None
    else:
        logger.info("YouTube Transcript API is available and functional")
        
except ImportError as e:
    logger.warning(f"YouTube Transcript API not available: {e}. YouTube processing will use Whisper fallback.")
    YouTubeTranscriptApi = None

# Constants
LANGUAGE_CODE_MAP = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
}

class YouTubeProcessor(FileProcessor):
    """
    Processor for YouTube videos.
    Handles transcript extraction, embedding generation, and key concept extraction.
    """
    
    def __init__(self, store: RepositoryManager):
        """
        Initialize the YouTube processor.
        
        Args:
            store: DocSynthStore instance for database operations
        """
        super().__init__()
        self.store = store
        
    async def process(self, 
                     user_id: str, 
                     file_id: str, 
                     filename: str, 
                     file_url: str, 
                     **kwargs) -> Dict[str, Any]:
        """
        Process a YouTube video from start to finish.
        
        Args:
            user_id: User ID
            file_id: File ID
            filename: YouTube URL or video title
            file_url: URL to access the video
            **kwargs: Additional parameters like language and comprehension_level
            
        Returns:
            Dict containing processing results
        """
        logger.info(f"Starting YouTube processing for: {filename} (ID: {file_id}, User: {user_id})")
        
        # Extract parameters
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        kwargs.get('user_gc_id', '')
        
        try:
            # Step 1: Extract content (transcript)
            content = await self.extract_content(
                filename=filename,
                language=language,
                file_url=file_url
            )
            
            # Get video info from content
            video_info = content.get('video_info', {})
            
            if not content or not content.get('transcript_data'):
                logger.error(f"Failed to extract transcript from YouTube video: {filename}")
                return {"success": False, "error": "Failed to extract transcript"}
                
            # Step 2: Generate embeddings
            processed_content = await self.generate_embeddings(content)
            
            # Step 3: Store segments and chunks
            await self._store_video_segments(user_id, file_id, filename, processed_content)
            
            # Step 4: Generate key concepts
            key_concepts = await self.generate_key_concepts(
                processed_content,
                language=language,
                comprehension_level=comprehension_level
            )
            
            # Log all extracted concepts
            if key_concepts and isinstance(key_concepts, list):
                logger.info(f"Extracted {len(key_concepts)} key concepts from video")
                for i, concept in enumerate(key_concepts):
                    title = concept.get('concept_title', '')
                    logger.debug(f"Processed concept {i+1}: title='{title[:50]}...' ")
            else:
                logger.warning(f"No key concepts were extracted from the video content")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to extract key concepts",
                    "metadata": {
                        "processor_type": "youtube",
                        "duration_seconds": video_info.get('duration_seconds', 0) if video_info else 0
                    }
                }
                    
            # Step 5: Process each key concept individually - save it and generate its learning materials
            concepts_processed = 0
            if key_concepts and isinstance(key_concepts, list):
                logger.info(f"Processing {len(key_concepts)} key concepts for file ID {file_id}")
                
                for i, concept in enumerate(key_concepts):
                    title = concept.get("concept_title", "")
                    explanation = concept.get("concept_explanation", "")
                    logger.info(f"Processing concept {i+1}/{len(key_concepts)}: '{title[:50]}...'")
                    logger.debug(f"Full concept data: title='{title}', explanation='{explanation[:100]}...'")
                    
                    # Log before saving to database
                    logger.debug(f"Saving concept to database: '{title[:30]}...'")
                    
                    # Import KeyConceptCreate
                    from api.schemas.learning_content import KeyConceptCreate
                    
                    # Skip if title or explanation is empty
                    if not title.strip() or not explanation.strip():
                        logger.warning(f"Skipping empty concept (title: '{title}', explanation: '{explanation}')")
                        continue
                        
                    try:
                        # Create KeyConceptCreate object
                        key_concept_data = KeyConceptCreate(
                            concept_title=title,
                            concept_explanation=explanation,
                            source_page_number=concept.get("source_page_number"),
                            source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                            source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds"),
                            is_custom=False  # Default to False for auto-generated concepts
                        )
                        
                        # Save the concept to get its ID
                        concept_id = await self.store.learning_material_repo.add_key_concept_async(
                            file_id=file_id,
                            key_concept_data=key_concept_data
                        )
                        
                        if not concept_id:
                            logger.error(f"Failed to save concept: {title}")
                            continue
                            
                    except Exception as e:
                        logger.error(f"Error saving concept '{title}': {str(e)}", exc_info=True)
                        continue
                    
                    if concept_id is not None:
                        logger.info(f"Saved concept '{title[:30]}...' with ID: {concept_id}")
                        logger.debug(f"Successfully saved concept to database with ID {concept_id}")
                        
                        # Generate and save learning materials for this concept immediately
                        concept_with_id = {
                            "concept_title": concept.get("concept_title", ""), 
                            "concept_explanation": concept.get("concept_explanation", ""),
                            "id": concept_id
                        }
                        
                        # Generate learning materials for this specific concept right away
                        logger.debug(f"Starting learning material generation for concept ID {concept_id}")
                        result = await self.generate_learning_materials_for_concept(file_id, concept_with_id)
                        
                        if result:
                            concepts_processed += 1
                            logger.info(f"Successfully generated learning materials for concept '{title[:30]}...'")
                        else:
                            logger.error(f"Failed to generate learning materials for concept '{title[:30]}...'")
                    else:
                        logger.error(f"Failed to save concept '{title[:30]}...' to database for file {file_id}")
                
                logger.info(f"Completed processing {concepts_processed}/{len(key_concepts)} key concepts for file {file_id}")
            else:
                logger.warning(f"No key concepts generated for YouTube file {file_id}")

            
            logger.info(f"YouTube processing completed successfully: {filename}")
            # Note: Status is now inferred from the presence of chunks and key concepts
        
            # Get segment and chunk counts for consistent return structure
            segment_count = len(processed_content.get('processed_segments', []))
            chunk_count = sum(len(segment.get('chunks', [])) for segment in processed_content.get('processed_segments', []))
        
            return {
                "success": True,
                "file_id": file_id,
                "metadata": {
                    "segment_count": segment_count,
                    "chunk_count": chunk_count,
                    "key_concepts_count": len(key_concepts) if key_concepts else 0,
                    "processor_type": "youtube"
                }
            }
            
        except Exception as e:
            self._log_error(f"Error processing YouTube video {filename}", e)
            # Note: Status is now inferred from the presence of chunks and key concepts
            return {
                "success": False,
                "file_id": file_id,
                "error": str(e),
                "metadata": {
                    "processor_type": "youtube"
                }
            }
    
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract transcript from a YouTube video.
        
        Args:
            **kwargs: Must include filename and language
            
        Returns:
            Dict containing transcript data and video ID
        """
        filename = kwargs.get('filename')
        language = kwargs.get('language', 'English')
        
        if not filename:
            raise ValueError("Filename (YouTube URL) is required")
            
        # Check if input is already a video ID (11 alphanumeric chars + hyphens/underscores)
        if re.fullmatch(r'[\w-]{11}', filename):
            video_id = filename
        else:
            # Try to extract video ID from URL
            yt_match = re.search(r'(?:v=|youtu.be/|embed/|/)([\w-]{11})', filename)
            video_id = yt_match.group(1) if yt_match else None
            
        if not video_id:
            raise ValueError(f"Invalid YouTube video ID or URL: {filename}")
            
        # Map language to code
        target_lang_code = LANGUAGE_CODE_MAP.get(language.lower(), language.lower())
        if not target_lang_code:  # Handle empty language string
            target_lang_code = 'en'
            
        # Try multiple methods to get transcript
        transcript_data = await self._get_youtube_transcript(video_id, target_lang_code)
        
        # If YouTube API fails, try Whisper
        if not transcript_data:
            transcript_data = await self._transcribe_with_whisper(video_id, target_lang_code)
            
        if not transcript_data:
            raise ValueError(f"Failed to extract transcript from video {video_id}")
            
        return {
            "transcript_data": transcript_data,
            "video_id": video_id
        }
    
    async def _get_youtube_transcript(self, video_id: str, target_lang_code: str) -> Optional[List[Dict[str, Any]]]:
        """
        Attempt to get transcript via YouTube API.
        
        Args:
            video_id: YouTube video ID
            target_lang_code: Language code for transcript (e.g., 'en')
            
        Returns:
            List of transcript segments or None if failed
        """
        if YouTubeTranscriptApi is None:
            logger.info("YouTubeTranscriptApi is not available, skipping transcript fetch")
            return None
            
        try:
            # First attempt: direct fetch with requested language
            logger.info(f"Attempting direct transcript fetch for {video_id} in language: {target_lang_code}")
            
            try:
                # Try new API first (youtube-transcript-api >= 0.6.0)
                if hasattr(YouTubeTranscriptApi, 'list_transcripts'):
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    transcript = transcript_list.find_transcript([target_lang_code])
                    transcript_data = transcript.fetch()
                # Fall back to old API (youtube-transcript-api < 0.6.0)
                elif hasattr(YouTubeTranscriptApi, 'get_transcript'):
                    logger.info("Using legacy YouTube Transcript API")
                    transcript_data = YouTubeTranscriptApi.get_transcript(
                        video_id, 
                        languages=[target_lang_code],
                        preserve_formatting=True
                    )
                else:
                    logger.warning("No usable YouTube Transcript API methods found")
                    return None
            except Exception as api_error:
                logger.warning(f"YouTube Transcript API error: {api_error}")
                return None
            
            if not transcript_data:
                logger.warning("No transcript data returned from YouTube API")
                return None
                
            logger.info(f"Successfully fetched transcript in {target_lang_code} (segments: {len(transcript_data)})")
            return transcript_data
            
        except Exception as direct_fetch_error:
            logger.warning(f"Direct transcript fetch failed: {direct_fetch_error}")
            
            # Second attempt: try English if the requested language wasn't English
            if target_lang_code != 'en':
                try:
                    logger.info(f"Attempting fallback to English transcript for {video_id}")
                    try:
                        # Try new API first if available
                        if hasattr(YouTubeTranscriptApi, 'list_transcripts'):
                            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                            transcript = transcript_list.find_transcript(['en'])
                            transcript_data = transcript.fetch()
                        # Fall back to old API
                        elif hasattr(YouTubeTranscriptApi, 'get_transcript'):
                            transcript_data = YouTubeTranscriptApi.get_transcript(
                                video_id,
                                languages=['en'],
                                preserve_formatting=True
                            )
                        else:
                            return None
                    except (AttributeError, ImportError):
                        # Fall back to old API
                        transcript_data = YouTubeTranscriptApi.get_transcript(
                            video_id,
                            languages=['en'],
                            preserve_formatting=True
                        )
                    
                    logger.info("Successfully fetched transcript in English as fallback.")
                    return transcript_data
                except Exception as english_fetch_error:
                    logger.warning(f"English transcript fallback also failed: {english_fetch_error}")
            
            return None
    
    async def _transcribe_with_whisper(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """
        Use Whisper for local transcription when YouTube API fails.
        
        Args:
            video_id: YouTube video ID
            target_lang_code: Language code for transcript (e.g., 'en', 'es')
            
        Returns:
            List of transcript segments with start/end times and text
            
        Raises:
            AgentError: If transcription fails
        """
        from ..agents.base_agent import AgentError
        import tempfile
        import os
        import asyncio
        import logging
        from typing import List, Dict, Any, Optional
        
        try:
            import whisper
        except ImportError as e:
            raise AgentError("Whisper is not available for transcription. Please install it with: pip install openai-whisper") from e
        
        temp_audio_file_path = None
        try:
            logger.info(f"Attempting fallback to local Whisper transcription for video ID: {video_id}")
            
            # Download YouTube audio
            temp_audio_path = await self._download_youtube_audio_segment(video_id, target_lang_code)
            if not temp_audio_path or not os.path.exists(temp_audio_path):
                raise AgentError("Failed to download audio for transcription")
                
            logger.info(f"Downloaded audio to {temp_audio_path} (Size: {os.path.getsize(temp_audio_path)} bytes)")
            
            # 2. Load model with progress tracking
            logger.info("Loading Whisper model...")
            
            # Import torch here to avoid import issues
            import torch
            from concurrent.futures import ThreadPoolExecutor
            
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Using device: {device}")
            
            # Load model in a separate thread to avoid blocking
            def load_model():
                return whisper.load_model("base", device=device)
                
            with ThreadPoolExecutor() as executor:
                model = await asyncio.get_event_loop().run_in_executor(executor, load_model)
                
            logger.info(f"Model loaded on {device.upper()}")
            
            # 3. Transcribe with progress
            logger.info("Starting transcription...")
            
            def transcribe():
                return model.transcribe(
                    temp_audio_path,
                    language=target_lang_code if target_lang_code != "auto" else None,
                    verbose=True,  # Show progress
                    fp16=torch.cuda.is_available()
                )
                
            # Run with timeout (1 hour max)
            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, transcribe),
                    timeout=3600
                )
                
                if not result or 'segments' not in result:
                    raise AgentError("Whisper returned empty result")
                    
                # 4. Process segments
                transcript_segments = []
                for segment in result['segments']:
                    transcript_segments.append({
                        'start': segment['start'],
                        'end': segment['end'],
                        'text': segment['text'].strip(),
                        'words': [{
                            'word': word['word'],
                            'start': word['start'],
                            'end': word['end'],
                            'probability': word.get('probability', 0.0)
                        } for word in segment.get('words', [])]
                    })
                
                logger.info(f"Successfully transcribed {len(transcript_segments)} segments")
                return transcript_segments
                
            except asyncio.TimeoutError:
                logger.error("Whisper transcription timed out after 1 hour")
                raise AgentError("Transcription timed out")
                
        except Exception as e:
            logger.error(f"Error in Whisper transcription: {str(e)}", exc_info=True)
            raise AgentError(f"Transcription failed: {str(e)}")
            
        finally:
            # Clean up temp files
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    temp_dir = os.path.dirname(temp_audio_path)
                    os.remove(temp_audio_path)
                    if os.path.exists(temp_dir):
                        os.rmdir(temp_dir)
                    logger.debug(f"Cleaned up temporary files in: {temp_dir}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary files: {e}")
                    
    async def _download_youtube_audio_segment(self, video_id: str, target_lang_code: str) -> Optional[str]:
        """
        Download YouTube audio with retries and progress tracking.
        
        Args:
            video_id: YouTube video ID
            target_lang_code: Target language code (unused, kept for compatibility)
            
        Returns:
            Path to the downloaded audio file or None if failed
        """
        import shutil
        import os
        import tempfile
        import subprocess
        import json
        from pathlib import Path
        from typing import Dict, Any, Optional, List
        
        # Try to import yt-dlp, provide helpful error if not available
        try:
            import yt_dlp
        except ImportError:
            logger.error("yt-dlp is not installed. Please install it with: pip install yt-dlp")
            return None
            
        # Verify FFmpeg is available
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            logger.error("FFmpeg is not installed or not in PATH. Audio conversion will fail.")
            return None
            
        logger.info(f"Starting audio download for video {video_id}")
        temp_dir = tempfile.mkdtemp(prefix=f"youtube_{video_id}_")
        temp_audio_path = os.path.join(temp_dir, f"{video_id}.%(ext)s")  # Will be filled by yt-dlp
        
        # Common yt-dlp options
        common_opts = {
            'quiet': False,
            'no_warnings': False,
            'noplaylist': True,
            'nocheckcertificate': True,
            'retries': 3,
            'fragment_retries': 3,
            'extractor_retries': 3,
            'ignoreerrors': False,
            'force_generic_extractor': False,
            'prefer_ffmpeg': True,
            'ffmpeg_location': ffmpeg_path,
            'outtmpl': temp_audio_path,
            'logger': None,  # Will be set per attempt
        }
        
        # Define multiple download attempts with different formats and options
        download_attempts: List[Dict[str, Any]] = [
            # 1. Try direct audio download with opus codec (most compatible)
            {
                'format': 'bestaudio[ext=webm][acodec=opus]/bestaudio',
                'options': {
                    **common_opts,
                    'format': 'bestaudio[ext=webm][acodec=opus]/bestaudio',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'wav',  # Convert to WAV first for maximum compatibility
                    }],
                    'postprocessor_args': [
                        '-ar', '16000',  # Resample to 16kHz
                        '-ac', '1',      # Convert to mono
                        '-b:a', '64k',   # Bitrate
                        '-f', 'wav'      # Force WAV output
                    ],
                    'extract_audio': True,
                    'audio_quality': '0',  # Best quality
                },
                'description': 'Opus audio with WAV conversion',
                'expected_ext': 'wav'
            },
            # 2. Try m4a format with aac codec
            {
                'format': 'bestaudio[ext=m4a]/bestaudio[acodec=mp4a.40.2]/bestaudio',
                'options': {
                    **common_opts,
                    'format': 'bestaudio[ext=m4a]/bestaudio[acodec=mp4a.40.2]/bestaudio',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'wav',
                    }],
                    'postprocessor_args': [
                        '-ar', '16000',
                        '-ac', '1',
                        '-b:a', '64k',
                        '-f', 'wav'
                    ],
                    'extract_audio': True,
                    'audio_quality': '0',
                },
                'description': 'M4A/AAC audio with WAV conversion',
                'expected_ext': 'wav'
            },
            # 3. Fallback to any audio format with direct download (no conversion)
            {
                'format': 'worstaudio/worst',  # Try worst quality first (smaller, faster)
                'options': {
                    **common_opts,
                    'format': 'worstaudio/worst',
                    'extract_audio': True,
                    'audio_quality': '0',
                    'prefer_ffmpeg': False,  # Don't use ffmpeg for extraction
                    'keepvideo': False,
                    'postprocessors': [],  # No post-processing
                    'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s')
                },
                'description': 'Direct audio download (any format)',
                'expected_ext': '*',  # Any extension
                'skip_ffmpeg': True  # Skip FFmpeg validation for this attempt
            }
        ]
        
        # Custom logger for yt-dlp
        class YTDLLogger:
            def debug(self, msg):
                # Skip common non-error messages
                skip_msgs = [
                    'Deleting original file',
                    'There are no fragments to download',
                    'Fragments'  # Skip fragment download progress
                ]
                if not any(skip in str(msg) for skip in skip_msgs):
                    logger.debug(f"yt-dlp: {msg}")
            
            def warning(self, msg):
                logger.warning(f"yt-dlp warning: {msg}")
            
            def error(self, msg):
                logger.error(f"yt-dlp error: {msg}")
        
        # Try each download attempt
        for attempt in download_attempts:
            attempt_ext = attempt.get('expected_ext', 'mp3')
            attempt_path = os.path.join(temp_dir, f"{video_id}.{attempt_ext}")
            
            # Clean up any existing files
            for f in Path(temp_dir).glob(f"{video_id}.*"):
                try:
                    f.unlink()
                except Exception as e:
                    logger.warning(f"Failed to clean up {f}: {e}")
            
            logger.info(f"Attempting download: {attempt['description']} (format: {attempt['format']})")
            
            try:
                # Set up logger for this attempt
                yt_dlp_opts = attempt['options'].copy()
                yt_dlp_opts['logger'] = YTDLLogger()
                
                # Log the options we're using (without sensitive data)
                log_opts = yt_dlp_opts.copy()
                if 'logger' in log_opts:
                    log_opts['logger'] = '<YTDLLogger>'
                logger.debug(f"Using yt-dlp options: {json.dumps(log_opts, indent=2, default=str)}")
                
                # Create downloader instance
                ydl = yt_dlp.YoutubeDL(yt_dlp_opts)
                
                # Run the download with timeout
                download_task = asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
                )
                
                # Wait for download with timeout
                try:
                    await asyncio.wait_for(download_task, timeout=600)  # 10 minute timeout
                except asyncio.TimeoutError:
                    logger.error(f"Download timed out after 10 minutes for format: {attempt['format']}")
                    continue
                
                # Find the downloaded file
                downloaded_files = list(Path(temp_dir).glob(f"{video_id}.*"))
                
                if not downloaded_files:
                    logger.warning(f"No files were downloaded in attempt: {attempt['description']}")
                    continue
                
                # Find the most recently modified file that's not a .part file
                downloaded_file = None
                for f in sorted(downloaded_files, key=os.path.getmtime, reverse=True):
                    if not f.name.endswith('.part') and f.stat().st_size > 1024:  # At least 1KB
                        downloaded_file = f
                        break
                
                if not downloaded_file:
                    logger.warning(f"No valid files found in {temp_dir} after download")
                    continue
                
                # Verify the file has content
                file_size = downloaded_file.stat().st_size
                if file_size < 1024:  # Less than 1KB is probably invalid
                    logger.warning(f"Downloaded file is too small: {file_size} bytes - {downloaded_file}")
                    continue
                
                logger.info(f"Successfully downloaded {file_size} bytes to {downloaded_file}")
                return str(downloaded_file)
                
            except Exception as e:
                logger.error(f"Download attempt failed: {str(e)}", exc_info=True)
                continue
        
        # If we get here, all attempts failed
        logger.error("All download attempts failed")
        
        # Clean up temp directory
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Error cleaning up temp directory: {e}")
        
        return None

    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for transcript segments.
        
        Args:
            content: Dict containing transcript data with 'transcript_data' key
                    Each item in transcript_data must be a dict with:
                    - start: float (start time in seconds)
                    - duration: float (duration in seconds)
                    - text: str (transcript text)
            
        Returns:
            Dict with 'processed_segments' containing:
            - video_id: str
            - processed_segments: List[Dict] with segment data and embeddings
        """
        from ..llm_compat import get_text_embeddings_in_batches
        
        try:
            # Ensure content is not a coroutine
            if hasattr(content, '__await__') or asyncio.iscoroutine(content):
                content = await content
                
            transcript_data = content.get('transcript_data', [])
            if not transcript_data:
                raise ValueError("Cannot generate embeddings: No transcript data provided")
            
            # Process transcript into segments with chunks
            processed_segments = []
            chunk_texts = []
            chunk_segment_map = []  # Maps chunk index to segment index
            
            # First pass: process all segments and collect chunks for embedding
            for segment_idx, entry in enumerate(transcript_data):
                if not isinstance(entry, dict):
                    logger.warning(f"Skipping non-dict transcript entry: {entry}")
                    continue
                    
                segment = {
                    'start_time': float(entry.get('start', 0)),
                    'duration': float(entry.get('duration', 0)),
                    'end_time': float(entry.get('start', 0)) + float(entry.get('duration', 0)),
                    'content': str(entry.get('text', '')).strip(),
                    'chunks': []
                }
                
                if not segment['content']:  # Skip empty segments
                    continue
                    
                # For now, treat each segment as a single chunk
                # This can be enhanced to split long segments into multiple chunks if needed
                chunk_texts.append(segment['content'])
                chunk_segment_map.append(segment_idx)
                
                processed_segments.append(segment)
            
            # Generate embeddings for all chunks in batches
            if chunk_texts:
                try:
                    # Ensure we're awaiting the coroutine if get_text_embeddings_in_batches is async
                    if asyncio.iscoroutinefunction(get_text_embeddings_in_batches):
                        chunk_embeddings = await get_text_embeddings_in_batches(chunk_texts)
                    else:
                        chunk_embeddings = get_text_embeddings_in_batches(chunk_texts)
                    
                    # Assign embeddings back to segments
                    for chunk_idx, embedding in enumerate(chunk_embeddings):
                        if not embedding:
                            logger.warning(f"Empty embedding for chunk {chunk_idx}")
                            continue
                            
                        segment_idx = chunk_segment_map[chunk_idx]
                        if segment_idx < len(processed_segments):
                            processed_segments[segment_idx]['chunks'].append({
                                'content': chunk_texts[chunk_idx],
                                'embedding': embedding
                            })
                except Exception as e:
                    logger.error(f"Error generating embeddings: {e}", exc_info=True)
                    # Continue with empty embeddings if generation fails
            
            # Log some stats
            total_chunks = sum(len(seg.get('chunks', [])) for seg in processed_segments)
            logger.info(f"Generated embeddings for {total_chunks} chunks across {len(processed_segments)} segments")
            
            return {
                'video_id': content.get('video_id'),
                'processed_segments': processed_segments
            }
            
        except Exception as e:
            logger.error(f"Error in generate_embeddings: {e}", exc_info=True)
            raise AgentError(f"Failed to generate embeddings: {str(e)}")
        
    async def _store_video_segments(self, user_id: str, file_id: str, filename: str, processed_content: Dict[str, Any]) -> None:
        """
        Store processed segments and chunks in the database.
        
            user_id: User ID
            file_id: File ID
            filename: Video title or URL
            processed_content: Processed segments with embeddings
        """
        segments = processed_content.get('processed_segments', [])
        if not segments:
            logger.warning(f"No segments to store for file_id: {file_id}")
            return
        
        logger.info(f"Storing {len(segments)} segments and their chunks for file_id: {file_id}")
        
        # Format data for file repository update - following the expected structure
        extracted_data = []
        
        for segment in segments:
            # Create segment data with chunks
            segment_data = {
                'content': segment['content'],
                'start_time': segment['start_time'],
                'end_time': segment['end_time'],
                'duration': segment['duration'],
                'chunks': []
            }
            
            # Add chunk data for this segment
            for chunk in segment.get('chunks', []):
                if 'embedding' not in chunk or not chunk['embedding']:
                    logger.warning(f"Missing embedding for chunk in file_id: {file_id}")
                    continue
                    
                segment_data['chunks'].append({
                    'content': chunk['content'],
                    'embedding': chunk['embedding']
                })
                
            extracted_data.append(segment_data)
        
        # Store using the proper repository method that matches the PDF processor
        success = await self.store.update_file_with_chunks_async(
            user_id=int(user_id),
            filename=filename,
            file_type="youtube",
            extracted_data=extracted_data
        )
        
        if success:
            logger.info(f"Successfully stored all segments and chunks for file_id: {file_id}")
        else:
            logger.error(f"Failed to store segments and chunks for file_id: {file_id}")
    
    async def generate_key_concepts(self, processed_content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the YouTube transcript.
        
        Args:
            processed_content: Dict with processed segments
            **kwargs: Additional parameters like language and comprehension_level
            
        Returns:
            List of key concepts
        """
        try:
            # Ensure processed_content is not a coroutine
            if hasattr(processed_content, '__await__') or asyncio.iscoroutine(processed_content):
                processed_content = await processed_content
                
            language = kwargs.get('language', 'English')
            comprehension_level = kwargs.get('comprehension_level', 'Beginner')
            segments = processed_content.get('processed_segments', [])
            
            if not segments:
                logger.warning("No segments available to generate key concepts.")
                return []
            
            # Extract content from segments for key concept generation
            segment_texts = []
            for segment in segments:
                if isinstance(segment, dict) and 'content' in segment:
                    segment_texts.append(segment['content'])
                else:
                    logger.warning(f"Skipping invalid segment: {segment}")
            
            if not segment_texts:
                logger.warning("No valid segment content found for key concept generation")
                return []
                
            full_text = '\n'.join(segment_texts)
            
            # Check if generate_key_concepts_dspy is a coroutine function
            if asyncio.iscoroutinefunction(generate_key_concepts_dspy):
                key_concepts = await generate_key_concepts_dspy(
                    document_text=full_text,
                    language=language,
                    comprehension_level=comprehension_level
                )
            else:
                key_concepts = generate_key_concepts_dspy(
                    document_text=full_text,
                    language=language,
                    comprehension_level=comprehension_level
                )
                
            if not isinstance(key_concepts, list):
                logger.error(f"Expected list of key concepts, got {type(key_concepts)}")
                return []
                
            logger.info(f"Generated {len(key_concepts)} key concepts from YouTube transcript")
            return key_concepts
            
        except Exception as e:
            self._log_error("Error generating key concepts", e)
            logger.error(f"Error details: {str(e)}", exc_info=True)
            return []
    
    async def generate_learning_materials_for_concept(self, file_id: str, concept: Dict[str, Any]) -> bool:
        """
        Generate and save learning materials for a single key concept.
        Delegates to the shared utility function.
        
        Args:
            file_id: ID of the file (as string in YouTube processor)
            concept: A single key concept with database ID
            
        Returns:
            bool: Success status
        """
        try:
            # Convert file_id to int since the shared utility expects an integer
            return await generate_learning_materials_for_concept(self.store, int(file_id), concept)
        except Exception as e:
            logger.error(f"Error generating learning materials: {e}", exc_info=True)
            return False
            
    async def generate_learning_materials(self, file_id: str, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate flashcards, MCQs, and T/F questions from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts that have already been stored in the database with their IDs
            
        Returns:
            Dict: Summary of concept processing results
        """
        if not key_concepts:
            logger.warning(f"No key concepts provided to generate learning materials for file {file_id}")
            return {"concepts_processed": 0, "concepts_successful": 0, "concepts_failed": 0}
            
        logger.info(f"Processing {len(key_concepts)} key concepts for file {file_id}")
        
        # Process each concept and track success/failure
        concept_results = []
        for concept in key_concepts:
            result = await self.generate_learning_materials_for_concept(file_id, concept)
            concept_results.append(result)
            
        # Use the shared utility to log summary and return results
        # Convert file_id to int for the utility function
        return await log_concept_processing_summary(concept_results, int(file_id))
        
    def _log_error(self, message: str, error: Exception) -> None:
        """
        Helper method to log errors with consistent formatting.
        
{{ ... }}
            message: Error message context
            error: Exception object
        """
        logger.error(f"{message}: {str(error)[:200]}", exc_info=True)
