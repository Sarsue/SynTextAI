"""
YouTube processor module - Handles extraction and processing of YouTube videos.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils.language_utils import validate_language, get_language_name
from ..llm_compat import get_text_embeddings_in_batches, generate_key_concepts_dspy
from ..repositories.repository_manager import RepositoryManager
from .base_processor import FileProcessor
from .processor_utils import (
    generate_learning_materials_for_concept as utils_generate_learning_materials_for_concept,
    log_concept_processing_summary,
)

# Import whisper/torch only when available
try:
    import whisper  # type: ignore
    import torch  # type: ignore
except ImportError:
    whisper = None
    torch = None

logger = logging.getLogger(__name__)

# Global instance of RepositoryManager for the standalone function
_repo_manager: Optional[RepositoryManager] = None


async def process_youtube(
    video_url: str,
    file_id: int,
    user_id: int,
    **kwargs,
) -> Dict[str, Any]:
    """
    Standalone function to process a YouTube video.

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
        from api.core.config import settings

        _repo_manager = get_repository_manager(database_url=settings.DATABASE_URL)

    processor = YouTubeProcessor(_repo_manager)
    # The processor.process signature uses user_id, file_id, filename, file_url
    return await processor.process(
        user_id=user_id,
        file_id=file_id,
        filename=video_url,  # Using video URL as "filename" for convenience
        file_url=video_url,
        **kwargs,
    )


# Import required only for YouTube processing
YouTubeTranscriptApi = None
try:
    # Try to import the transcript API
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

    # Test if the required methods are available
    required_methods = ["list_transcripts", "get_transcript"]
    if all(hasattr(YouTubeTranscriptApi, method) for method in required_methods):
        # Test with a known public video ID to verify API functionality
        test_video_id = "dQw4w9WgXcQ"
        try:
            # Test if we can list transcripts
            YouTubeTranscriptApi.list_transcripts(test_video_id)
            logger.info("YouTube Transcript API is available and functional")
        except Exception as api_error:
            logger.warning(
                f"YouTube Transcript API test failed: {str(api_error)}. Falling back to Whisper."
            )
            YouTubeTranscriptApi = None
    else:
        missing_methods = [m for m in required_methods if not hasattr(YouTubeTranscriptApi, m)]
        logger.warning(
            f"YouTube Transcript API is missing required methods: {', '.join(missing_methods)}. Falling back to Whisper."
        )
        YouTubeTranscriptApi = None

except ImportError as e:
    logger.warning(f"YouTube Transcript API not installed: {e}. Falling back to Whisper.")
    YouTubeTranscriptApi = None

except Exception as e:
    logger.warning(f"Unexpected error initializing YouTube Transcript API: {str(e)}. Falling back to Whisper.")
    YouTubeTranscriptApi = None


class TemporaryAudioFile:
    """Context manager for temporary audio files (and directory)."""

    def __init__(self, video_id: str, extension: str = "wav"):
        self.video_id = video_id
        self.extension = extension.lstrip(".")
        self.temp_dir: Optional[str] = None
        self.file_path: Optional[str] = None

    def __enter__(self) -> str:
        self.temp_dir = tempfile.mkdtemp(prefix=f"youtube_{self.video_id}_")
        self.file_path = os.path.join(self.temp_dir, f"{self.video_id}.{self.extension}")
        return self.file_path

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                logger.debug(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {self.temp_dir}: {e}")
        self.temp_dir = None
        self.file_path = None


class YouTubeProcessor(FileProcessor):
    """
    Processor for YouTube videos.
    Handles transcript extraction, embedding generation, and key concept extraction.
    """

    def __init__(self, store: RepositoryManager):
        super().__init__()
        self.store = store

    async def process(
        self,
        user_id: int,
        file_id: int,
        filename: str,
        file_url: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Process a YouTube video from start to finish.
        """
        logger.info(f"Starting YouTube processing for: {filename} (ID: {file_id}, User: {user_id})")

        language = kwargs.get("language", "English")
        comprehension_level = kwargs.get("comprehension_level", "Beginner")
        kwargs.get("user_gc_id", "")

        try:
            # Step 1: Extract transcript
            content = await self.extract_content(filename=filename, language=language, file_url=file_url)

            if not content or not content.get("transcript_data"):
                logger.error(f"Failed to extract transcript from YouTube video: {filename}")
                return {"success": False, "error": "Failed to extract transcript"}

            # Step 2: Generate embeddings and build processed segments
            processed_content = await self.generate_embeddings(content, language=language, comprehension_level=comprehension_level)

            # Step 3: Store segments and chunks
            await self._store_video_segments(user_id, file_id, filename, processed_content)

            # Step 4: Generate key concepts from processed content
            # First check if we have valid transcript content
            segments = processed_content.get("processed_segments", [])
            full_text = "\n\n".join(
                s.get("content", "").strip() 
                for s in segments 
                if isinstance(s, dict) and s.get("content")
            ).strip()

            if not full_text:
                logger.warning(f"No transcript content available for key concept generation for file {file_id}")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Transcript empty, cannot generate key concepts",
                    "metadata": {
                        "processor_type": "youtube",
                    },
                }

            # Now generate key concepts with the validated content
            key_concepts = await self.generate_key_concepts(
                processed_content, language=language, comprehension_level=comprehension_level
            )

            if key_concepts and isinstance(key_concepts, list):
                logger.info(f"Extracted {len(key_concepts)} key concepts from video")
                for i, concept in enumerate(key_concepts):
                    title = str(concept.get("concept_title", ""))[:80]
                    logger.debug(f"Processed concept {i+1}: title='{title}'")
            else:
                logger.warning("No key concepts were extracted from the video content")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to extract key concepts",
                    "metadata": {
                        "processor_type": "youtube",
                    },
                }

            # Step 5: Save each concept and immediately generate its learning materials
            concepts_processed = 0
            for i, concept in enumerate(key_concepts):
                title = concept.get("concept_title", "")
                explanation = concept.get("concept_explanation", "")
                if not title.strip() or not explanation.strip():
                    logger.warning(f"Skipping empty concept (title: '{title}', explanation length: {len(explanation)})")
                    continue

                try:
                    # Create a dictionary with the key concept data
                    key_concept_data = {
                        "concept_title": title,
                        "concept_explanation": explanation,
                        "source_page_number": concept.get("source_page_number"),
                        "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds"),
                        "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds"),
                        "is_custom": False
                    }
                    
                    # Add the key concept using the learning material repository
                    try:
                        result = await self.store.learning_material_repo.add_key_concept(
                            file_id=file_id,
                            key_concept_data=key_concept_data
                        )
                        concept_id = result.get("id") if result else None
                        if not concept_id:
                            raise ValueError("Failed to get concept ID from repository")
                    except Exception as e:
                        logger.error(f"Error saving key concept to database: {e}", exc_info=True)
                        raise
                except Exception as e:
                    logger.error(f"Error saving concept '{title}': {str(e)}", exc_info=True)
                    continue

                if concept_id is None:
                    logger.error(f"Failed to save concept '{title[:30]}...' to database for file {file_id}")
                    continue

                concept_with_id = {
                    "concept_title": title,
                    "concept_explanation": explanation,
                    "id": concept_id,
                }

                logger.debug(f"Starting learning material generation for concept ID {concept_id}")
                result = await self.generate_learning_materials_for_concept(file_id, concept_with_id)
                if result:
                    concepts_processed += 1
                    logger.info(f"Generated learning materials for concept '{title[:30]}...'")
                else:
                    logger.error(f"Failed to generate learning materials for concept '{title[:30]}...'")

            logger.info(f"Completed processing {concepts_processed}/{len(key_concepts)} key concepts for file {file_id}")

            segment_count = len(processed_content.get("processed_segments", []))
            chunk_count = sum(len(s.get("chunks", [])) for s in processed_content.get("processed_segments", []))

            return {
                "success": True,
                "file_id": file_id,
                "metadata": {
                    "segment_count": segment_count,
                    "chunk_count": chunk_count,
                    "key_concepts_count": len(key_concepts) if key_concepts else 0,
                    "processor_type": "youtube",
                },
            }

        except Exception as e:
            self._log_error(f"Error processing YouTube video {filename}", e)
            return {
                "success": False,
                "file_id": file_id,
                "error": str(e),
                "metadata": {"processor_type": "youtube"},
            }

    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract transcript from a YouTube video.
        Returns {'transcript_data': List[segment], 'video_id': str}
        """
        filename = kwargs.get("filename")
        language = kwargs.get("language", "English")

        if not filename:
            raise ValueError("Filename (YouTube URL) is required")

        # Check if already a video ID
        if re.fullmatch(r"[\w-]{11}", filename):
            video_id = filename
        else:
            # Extract ID from URL
            yt_match = re.search(r"(?:v=|youtu\.be/|embed/|/)([\w-]{11})", filename)
            video_id = yt_match.group(1) if yt_match else None

        if not video_id:
            raise ValueError(f"Invalid YouTube video ID or URL: {filename}")

        # Validate language
        language = validate_language(language)
        if not language:
            raise ValueError("Invalid language")

        # Map language to code
        target_lang_code = get_language_name(language) or "en"

        # Try YouTube transcripts
        transcript_data = await self._get_youtube_transcript(video_id, target_lang_code)

        # Fallback: Whisper transcription
        if not transcript_data:
            transcript_data = await self._transcribe_with_whisper(video_id, target_lang_code)

        if not transcript_data:
            raise ValueError(f"Failed to extract transcript from video {video_id}")

        return {"transcript_data": transcript_data, "video_id": video_id}

    async def _get_youtube_transcript(self, video_id: str, target_lang_code: str) -> Optional[List[Dict[str, Any]]]:
        """
        Attempt to get transcript via YouTube Transcript API.
        """
        if YouTubeTranscriptApi is None:
            logger.info("YouTubeTranscriptApi is not available, skipping transcript fetch")
            return None

        try:
            logger.info(f"Attempting direct transcript fetch for {video_id} in language: {target_lang_code}")

            try:
                if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    transcript = transcript_list.find_transcript([target_lang_code])
                    transcript_data = transcript.fetch()
                elif hasattr(YouTubeTranscriptApi, "get_transcript"):
                    logger.info("Using legacy YouTube Transcript API")
                    transcript_data = YouTubeTranscriptApi.get_transcript(
                        video_id, languages=[target_lang_code], preserve_formatting=True
                    )
                else:
                    logger.warning("No usable YouTube Transcript API methods found")
                    return None
            except Exception as api_error:
                logger.warning(f"YouTube Transcript API error: {api_error}")
                transcript_data = None

            if not transcript_data:
                logger.warning("No transcript data returned from YouTube API")
                return None

            logger.info(f"Fetched transcript in {target_lang_code} (segments: {len(transcript_data)})")
            return transcript_data

        except Exception as direct_fetch_error:
            logger.warning(f"Direct transcript fetch failed: {direct_fetch_error}")

            if target_lang_code != "en":
                try:
                    logger.info(f"Attempting fallback to English transcript for {video_id}")
                    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                        transcript = transcript_list.find_transcript(["en"])
                        transcript_data = transcript.fetch()
                    elif hasattr(YouTubeTranscriptApi, "get_transcript"):
                        transcript_data = YouTubeTranscriptApi.get_transcript(
                            video_id, languages=["en"], preserve_formatting=True
                        )
                    else:
                        return None

                    logger.info("Successfully fetched English transcript as fallback.")
                    return transcript_data
                except Exception as english_fetch_error:
                    logger.warning(f"English transcript fallback also failed: {english_fetch_error}")

            return None

    async def _transcribe_with_whisper(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """
        Use Whisper for local transcription when YouTube API fails.
        Returns list of segments: [{'start': float, 'end': float, 'text': str, 'words': [...]}, ...]
        """
        class AgentError(Exception):
            pass

        if whisper is None:
            raise AgentError("Whisper is not available. Install with: pip install openai-whisper")

        logger.info(f"Fallback to local Whisper transcription for video ID: {video_id}")

        # Keep temp audio alive for the whole transcription
        with TemporaryAudioFile(video_id, "wav") as temp_audio_path:
            # Download audio into the temp directory
            ok = await self._download_youtube_audio_segment(video_id, temp_audio_path)
            if not ok or not os.path.exists(temp_audio_path):
                raise AgentError("Failed to download audio for transcription")

            logger.info(f"Downloaded audio to {temp_audio_path} (Size: {os.path.getsize(temp_audio_path)} bytes)")

            # Determine device
            device = "cuda" if (torch is not None and torch.cuda.is_available()) else "cpu"
            logger.info(f"Using device: {device}")

            # Load model in a separate thread
            def load_model():
                return whisper.load_model("base", device=device)

            with ThreadPoolExecutor() as executor:
                model = await asyncio.get_event_loop().run_in_executor(executor, load_model)

            logger.info(f"Whisper model loaded on {device.upper()}")

            # Transcribe (run in executor with timeout)
            def transcribe():
                return model.transcribe(
                    temp_audio_path,
                    language=None if target_lang_code == "auto" else target_lang_code,
                    verbose=False,
                    fp16=(torch is not None and torch.cuda.is_available()),
                )

            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, transcribe),
                    timeout=3600,
                )
            except asyncio.TimeoutError:
                logger.error("Whisper transcription timed out after 1 hour")
                raise AgentError("Transcription timed out")

            if not result or "segments" not in result:
                raise AgentError("Whisper returned empty result")

            transcript_segments: List[Dict[str, Any]] = []
            for segment in result["segments"]:
                transcript_segments.append(
                    {
                        "start": float(segment["start"]),
                        "end": float(segment["end"]),
                        "text": str(segment["text"]).strip(),
                        "words": [
                            {
                                "word": w.get("word"),
                                "start": w.get("start"),
                                "end": w.get("end"),
                                "probability": w.get("probability", 0.0),
                            }
                            for w in segment.get("words", []) or []
                        ],
                    }
                )

            logger.info(f"Successfully transcribed {len(transcript_segments)} segments via Whisper")
            return transcript_segments

    async def _download_youtube_audio_segment(self, video_id: str, final_wav_path: str) -> bool:
        """
        Download YouTube audio into the directory of final_wav_path and create a 16kHz mono WAV there.
        Returns True on success, False otherwise.
        """
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            logger.error("yt-dlp is not installed. Please install it with: pip install yt-dlp")
            return False

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.error("FFmpeg is not installed or not in PATH. Audio conversion will fail.")
            return False

        out_dir = os.path.dirname(final_wav_path)
        video_basename = os.path.splitext(os.path.basename(final_wav_path))[0]
        # Let yt-dlp write to <dir>/<video_id>.<ext>, then we will ensure WAV
        template = os.path.join(out_dir, f"{video_id}.%(ext)s")

        # Options with post-processor to WAV 16kHz mono
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "0"},
            ],
            "postprocessor_args": ["-ar", "16000", "-ac", "1"],  # 16kHz mono
            "ffmpeg_location": ffmpeg_path,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "ignoreerrors": False,
        }

        logger.info(f"Starting audio download for video {video_id}")
        ydl = yt_dlp.YoutubeDL(ydl_opts)

        async def run_download() -> None:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            )

        try:
            await asyncio.wait_for(run_download(), timeout=600)  # 10 minutes
        except asyncio.TimeoutError:
            logger.error("Audio download timed out after 10 minutes")
            return False
        except Exception as e:
            logger.error(f"yt-dlp download failed: {e}", exc_info=True)
            return False

        produced_wav = os.path.join(out_dir, f"{video_id}.wav")
        if os.path.exists(produced_wav):
            try:
                # Move/rename to the requested final_wav_path (if different)
                if os.path.abspath(produced_wav) != os.path.abspath(final_wav_path):
                    shutil.move(produced_wav, final_wav_path)
                logger.info(f"Audio prepared at {final_wav_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to move produced WAV: {e}", exc_info=True)
                return False

        logger.error("yt-dlp did not produce a WAV file")
        return False

    async def _store_video_segments(
        self, user_id: int, file_id: int, filename: str, processed_content: Dict[str, Any]
    ) -> None:
        """
        Store processed segments and chunks in the database.
        """
        segments = processed_content.get("processed_segments", [])
        if not segments:
            logger.warning(f"No segments to store for file_id: {file_id}")
            return

        logger.info(f"Storing {len(segments)} segments and their chunks for file_id: {file_id}")

        extracted_data: List[Dict[str, Any]] = []
        file_type = "youtube"  # Since this is the YouTube processor
        for segment in segments:
            segment_data = {
                "content": segment["content"],
                "page_number": 0,  # Default page number for video segments
                "metadata": {
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": segment["duration"]
                },
                "chunks": [],
            }

            for chunk in segment.get("chunks", []):
                emb = chunk.get("embedding")
                if not emb:
                    logger.warning(f"Missing embedding for chunk in file_id: {file_id}")
                    continue
                segment_data["chunks"].append({"content": chunk["content"], "embedding": emb})

            extracted_data.append(segment_data)

            # Store the segments and chunks using the file repository
            success = await self.store.file_repo.update_file_with_chunks(
                user_id=user_id,
                filename=filename,
                file_type=file_type,
                extracted_data=extracted_data
            )
            
            if not success:
                logger.error(f"Failed to store video segments for file_id: {file_id}")
                return

        logger.info(f"Successfully stored all segments and chunks for file_id: {file_id}")

    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from the video transcript content.
        Expects content containing 'processed_segments'.
        
        Returns:
            List of dictionaries containing key concepts, or empty list if no content
            
        Raises:
            ValueError: If content is invalid or empty
        """
        try:
            if not content or not isinstance(content, dict):
                logger.warning("Invalid content: expected dict with 'processed_segments'")
                return []

            segments = content.get("processed_segments", [])
            if not isinstance(segments, list):
                logger.warning("'processed_segments' must be a list")
                return []
                
            if not segments:
                logger.warning("No segments found in processed content")
                return []

            language = str(kwargs.get("language", "English")).strip()
            comprehension_level = str(kwargs.get("comprehension_level", "Beginner")).strip()

            # Build full text with timestamps and validate content
            valid_segments = [
                s for s in segments 
                if isinstance(s, dict) and s.get('content') and str(s.get('content', '')).strip()
            ]
            
            if not valid_segments:
                logger.warning("No valid segments with content found in transcript")
                return []

            full_text = "\n\n".join(
                f"[{s.get('metadata', {}).get('start_time', 0):.1f}s] {s.get('content', '').strip()}" 
                for s in valid_segments
            ).strip()
            
            if not full_text:
                logger.warning("No transcript content available for key concept generation after processing")
                return []

            logger.info(f"Generating key concepts from {len(valid_segments)} segments ({len(full_text)} chars)")

            try:
                key_concepts = await generate_key_concepts_dspy(
                    document_text=full_text, 
                    language=language, 
                    comprehension_level=comprehension_level
                )
                
                if not key_concepts:
                    logger.warning("No key concepts were generated from the transcript")
                    return []
                    
                logger.info(f"Successfully generated {len(key_concepts)} key concepts")
                return key_concepts
                
            except Exception as e:
                logger.error(f"Error during key concept generation: {str(e)}", exc_info=True)
                # Include the first 100 chars of the transcript in the error for debugging
                sample_text = full_text[:100] + ('...' if len(full_text) > 100 else '')
                logger.debug(f"Transcript sample (first 100 chars): {sample_text}")
                return []

            return key_concepts

        except Exception as e:
            self._log_error("Error in generate_key_concepts", e)
            return []

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 800, overlap: int = 150) -> List[str]:
        """
        Split text into overlapping chunks.
        """
        text = (text or "").strip()
        if not text:
            return []
        chunks: List[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + max_chars, n)
            chunks.append(text[start:end])
            if end == n:
                break
            start = max(0, end - overlap)
        return chunks

    async def generate_embeddings(self, content: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Build processed segments from transcript_data and generate embeddings for chunks.
        Returns content dict with 'processed_segments' populated (with chunks+embeddings).
        """
        try:
            if not content or not isinstance(content, dict):
                logger.warning("Invalid content: expected dict with transcript_data")
                return content

            transcript_data = content.get("transcript_data", [])
            if not isinstance(transcript_data, list) or not transcript_data:
                logger.warning("No transcript_data to process for embeddings.")
                return content

            # Convert raw transcript segments to processed_segments
            processed_segments: List[Dict[str, Any]] = []
            for seg in transcript_data:
                # transcript segments could be shape from YT API or Whisper
                start = float(seg.get("start", seg.get("start_time", 0.0)))
                end = float(seg.get("end", seg.get("end_time", start)))
                text = str(seg.get("text", seg.get("content", ""))).strip()
                if end < start:
                    end = start
                duration = max(0.0, end - start)
                processed_segments.append(
                    {
                        "content": text,
                        "start_time": start,
                        "end_time": end,
                        "duration": duration,
                        "chunks": [],  # will fill next
                    }
                )

            # Create chunks across segments and gather for batch embedding
            all_chunks: List[Tuple[int, int, str]] = []  # (segment_index, chunk_index, content)
            for si, seg in enumerate(processed_segments):
                chunks = self._chunk_text(seg["content"])
                seg["chunks"] = [{"content": c} for c in chunks]
                for ci, ch in enumerate(chunks):
                    all_chunks.append((si, ci, ch))

            if not all_chunks:
                logger.warning("No text chunks available for embedding generation.")
                content["processed_segments"] = processed_segments
                return content

            texts_to_embed = [c for (_, _, c) in all_chunks]
            logger.info(f"Generating embeddings for {len(texts_to_embed)} chunks")

            # Batch embeddings (async function)
            embeddings: List[List[float]] = await get_text_embeddings_in_batches(texts_to_embed)

            if len(embeddings) != len(all_chunks):
                logger.error(
                    f"Mismatch between chunks ({len(all_chunks)}) and embeddings ({len(embeddings)})"
                )
                content["processed_segments"] = processed_segments
                return content

            # Place embeddings back into segments
            for (si, ci, _), emb in zip(all_chunks, embeddings):
                processed_segments[si]["chunks"][ci]["embedding"] = emb

            # Return updated content
            result = content.copy()
            result["processed_segments"] = processed_segments
            logger.info(f"Successfully generated embeddings for {len(all_chunks)} chunks")
            return result

        except Exception as e:
            self._log_error("Error generating embeddings", e)
            return content

    async def generate_learning_materials_for_concept(self, file_id: int, concept: Dict[str, Any]) -> bool:
        """
        Generate and save learning materials for a single key concept (async wrapper).
        """
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: utils_generate_learning_materials_for_concept(self.store, int(file_id), concept))
        except Exception as e:
            logger.error(f"Error generating learning materials: {e}", exc_info=True)
            return False

    async def generate_learning_materials(self, file_id: int, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        (Optional bulk path) Generate flashcards, MCQs, and T/F questions from key concepts.
        """
        if not key_concepts:
            logger.warning(f"No key concepts provided to generate learning materials for file {file_id}")
            return {"concepts_processed": 0, "concepts_successful": 0, "concepts_failed": 0}

        logger.info(f"Processing {len(key_concepts)} key concepts for file {file_id}")
        concept_results = []
        for concept in key_concepts:
            try:
                ok = await self.generate_learning_materials_for_concept(int(file_id), concept)
            except Exception as e:
                logger.error(f"Error generating materials for concept: {e}", exc_info=True)
                ok = False
            concept_results.append(ok)

        return log_concept_processing_summary(concept_results, int(file_id))

    def _log_error(self, message: str, error: Exception) -> None:
        """
        Helper method to log errors with consistent formatting.
        """
        logger.error(f"{message}: {str(error)[:200]}", exc_info=True)
