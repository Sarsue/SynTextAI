import asyncio
import logging
import os
import re
import gc
from typing import Dict, List, Any, Optional, Tuple
import yt_dlp
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.processors.processor_utils import generate_learning_materials_for_concepts, generate_learning_materials_for_concept, log_concept_processing_summary
from api.llm_service import generate_key_concepts, get_text_embeddings_in_batches, _deduplicate_concepts, _validate_references, _standardize_concept_format
from api.schemas.learning_content import KeyConceptCreate
try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
except ImportError:
    logging.getLogger(__name__).warning("YouTube Transcript API not available. YouTube processing will be limited.")
    YouTubeTranscriptApi = None

logger = logging.getLogger(__name__)

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

# Maximum number of key concepts to persist per YouTube video
MAX_CONCEPTS_PER_VIDEO = 60

# Maximum allowed YouTube video duration in seconds (2 hours)
MAX_VIDEO_DURATION_SECONDS = 7200

# Maximum number of key concepts to persist per YouTube video
MAX_CONCEPTS_PER_VIDEO = 60

class YouTubeProcessor(FileProcessor):
    """Processor for YouTube videos."""
    
    def __init__(self, store: RepositoryManager):
        super().__init__()
        self.store = store
        
    async def process(self, user_id: str, file_id: str, filename: str, file_url: str, **kwargs) -> Dict[str, Any]:
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

        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        user_gc_id = kwargs.get('user_gc_id', '')

        try:
            file_id_int = int(file_id)  # Ensure file_id is an integer

            # Step 1: Extract content (transcript)
            # 'extracting' status is set by caller (process_file_data). Keep it there.
            content = await self.extract_content(filename=filename, language=language)
            if not content or not content.get('transcript_data'):
                logger.error(f"Failed to extract transcript from YouTube video: {filename}")
                await self.store.file_repo.update_file_status(file_id_int, "failed")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to extract transcript",
                    "metadata": {"processor_type": "youtube", "segment_count": 0}
                }

            # Step 2: Generate embeddings
            # Update status to 'embedding' to support REST polling progress
            await self.store.file_repo.update_file_status(file_id_int, "embedding")
            processed_content = await self.generate_embeddings(content)
            if not processed_content.get('chunks'):
                logger.error(f"No chunks generated for {filename}")
                await self.store.file_repo.update_file_status(file_id_int, "failed")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to generate chunks",
                    "metadata": {"processor_type": "youtube", "chunk_count": 0}
                }

            # Step 3: Store segments and chunks
            await self.store.file_repo.update_file_status(file_id_int, "storing")
            await self._store_video_segments(user_id, file_id, filename, processed_content)

            # Step 4: Generate key concepts
            await self.store.file_repo.update_file_status(file_id_int, "generating_concepts")
            key_concepts = await self.generate_key_concepts(
                content,
                language=language,
                comprehension_level=comprehension_level
            )

            # Enforce a sane upper bound on number of concepts per video (match PDF behavior)
            if key_concepts and isinstance(key_concepts, list) and len(key_concepts) > MAX_CONCEPTS_PER_VIDEO:
                logger.info(
                    f"Truncating key concepts list from {len(key_concepts)} to {MAX_CONCEPTS_PER_VIDEO} for file {file_id_int}"
                )
                key_concepts = key_concepts[:MAX_CONCEPTS_PER_VIDEO]

            if not key_concepts:
                logger.error(f"No key concepts generated for {filename}")
                await self.store.file_repo.update_file_status(file_id_int, "failed")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to extract key concepts",
                    "metadata": {
                        "processor_type": "youtube",
                        "chunk_count": len(processed_content.get('chunks', [])),
                        "transcript_length": len('\n'.join([entry['text'] for entry in content.get('transcript_data', [])]))
                    }
                }

            # Log extracted concepts
            logger.info(f"Extracted {len(key_concepts)} key concepts from video")
            for i, concept in enumerate(key_concepts):
                title = concept.get('concept_title', '')
                logger.debug(f"Processed concept {i+1}: title='{title[:50]}...'")

            # Step 5: Process key concepts and generate learning materials
            saved_concepts: List[Dict[str, Any]] = []
            for i, concept in enumerate(key_concepts):
                title = concept.get("concept_title", "")
                explanation = concept.get("concept_explanation", "")
                logger.info(f"Processing concept {i+1}/{len(key_concepts)}: '{title[:50]}...'")

                # Clean up concept title and explanation
                title = title.strip().replace('**', '').replace('*', '').replace('`', '')
                explanation = explanation.strip().replace('**', '').replace('*', '').replace('`', '')

                # Skip malformed concepts
                if title.isdigit() or len(title) < 3 or title.startswith('0') and len(title) <= 2:
                    logger.warning(f"Skipping malformed concept: title='{title}', explanation='{explanation[:50]}...'")
                    continue

                # Ensure timestamps are valid numbers
                start_time = concept.get("source_video_timestamp_start_seconds", 0)
                end_time = concept.get("source_video_timestamp_end_seconds", 0)

                try:
                    start_time = float(start_time) if start_time else 0
                    end_time = float(end_time) if end_time else 0
                except (ValueError, TypeError):
                    logger.warning(f"Invalid timestamp values: start={start_time}, end={end_time}. Using defaults.")
                    start_time = end_time = 0

                try:
                    # Save concept (repo handles its own session/transaction)
                    key_concept_create = KeyConceptCreate(
                        concept_title=title[:255],  # Truncate to avoid length issues
                        concept_explanation=explanation,
                        source_page_number=concept.get("source_page_number"),
                        source_video_timestamp_start_seconds=start_time,
                        source_video_timestamp_end_seconds=end_time,
                        is_custom=False
                    )
                    concept_result = await self.store.learning_material_repo.add_key_concept(
                        file_id=file_id_int,
                        key_concept_data=key_concept_create
                    )
                    concept_id = concept_result.get('id') if concept_result else None

                    if concept_id:
                        logger.info(f"Saved concept '{title[:30]}...' with ID: {concept_id}")
                        saved_concepts.append(
                            {
                                "id": concept_id,
                                "concept_title": title,
                                "concept_explanation": explanation,
                            }
                        )
                    else:
                        logger.error(f"Failed to save concept '{title[:30]}...' to database")
                except Exception as e:
                    logger.error(f"Error processing concept '{title[:30]}...': {e}")
                    continue

            if saved_concepts:
                await generate_learning_materials_for_concepts(
                    store=self.store,
                    file_id=int(file_id_int),
                    concepts=saved_concepts,
                    comprehension_level=comprehension_level,
                )

            chunk_count = len(processed_content.get('chunks', []))
            transcript_length = len('\n'.join([entry['text'] for entry in content.get('transcript_data', [])]))

            return {
                "success": True,
                "file_id": file_id,
                "metadata": {
                    "chunk_count": chunk_count,
                    "key_concepts_count": len(key_concepts),
                    "processor_type": "youtube",
                    "transcript_length": transcript_length
                }
            }

        except Exception as e:
            self._log_error(f"Error processing YouTube video {filename}", e)
            transcript_length = 0
            if 'content' in locals() and content and content.get('transcript_data'):
                transcript_length = len('\n'.join([entry['text'] for entry in content.get('transcript_data', [])]))
            await self.store.file_repo.update_file_status(file_id_int, "failed")
            return {
                "success": False,
                "file_id": file_id,
                "error": str(e)[:200],
                "metadata": {
                    "processor_type": "youtube",
                    "chunk_count": len(processed_content.get('chunks', [])) if 'processed_content' in locals() else 0,
                    "transcript_length": transcript_length
                }
            }
    
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """Extract transcript from a YouTube video."""
        filename = kwargs.get('filename')
        language = kwargs.get('language', 'English')
        
        if not filename:
            raise ValueError("Filename (YouTube URL) is required")
            
        yt_match = re.search(r'(?:v=|youtu.be/|embed/)([\w-]{11})', filename)
        video_id = yt_match.group(1) if yt_match else None
        
        if not video_id:
            raise ValueError(f"Could not extract video ID from YouTube URL: {filename}")

        # Enforce maximum video duration before doing any heavy processing
        duration_seconds = self._get_video_duration_seconds(video_id)
        if duration_seconds is not None and duration_seconds > MAX_VIDEO_DURATION_SECONDS:
            logger.warning(
                f"YouTube video {video_id} duration {duration_seconds} seconds exceeds limit of {MAX_VIDEO_DURATION_SECONDS} seconds"
            )
            raise ValueError(
                f"Video is too long ({int(duration_seconds // 60)} minutes). Maximum allowed duration is {int(MAX_VIDEO_DURATION_SECONDS // 60)} minutes."
            )
            
        target_lang_code = LANGUAGE_CODE_MAP.get(language.lower(), 'en')
        
        # Add light retry/backoff for robustness with long videos
        attempt = 0
        last_error = None
        transcript_data = None
        while attempt < 3 and not transcript_data:
            try:
                transcript_data = await self._transcribe_with_whisper(video_id, target_lang_code)
                if not transcript_data:
                    logger.warning(f"Whisper transcription failed for {video_id}, falling back to YouTube API (attempt {attempt+1})")
                    transcript_data = await self._get_youtube_transcript(video_id, target_lang_code)
                if transcript_data:
                    full_text_check = '\n'.join([entry['text'] for entry in transcript_data])
                    if not full_text_check.strip():
                        logger.warning(f"Transcript empty for {video_id} (attempt {attempt+1})")
                        transcript_data = None
                if transcript_data:
                    break
            except Exception as e:
                last_error = e
                transcript_data = None
            if not transcript_data:
                await asyncio.sleep(2 ** attempt)
                attempt += 1
        if not transcript_data:
            err = last_error or Exception("Transcript extraction failed after retries")
            logger.error(str(err))
            raise ValueError(f"Failed to extract transcript from video {video_id}")

        # Calculate full text for logging
        full_text_check = '\n'.join([entry['text'] for entry in transcript_data])

        # Log transcript summary (keep INFO concise; move large payload logs to DEBUG)
        logger.info(
            f"Transcript extracted for video {video_id}: {len(transcript_data)} segments, {len(full_text_check)} characters"
        )
        logger.debug(f"Transcript first segment type: {type(transcript_data[0]) if transcript_data else 'None'}")
        logger.debug(f"Transcript first segment keys: {list(transcript_data[0].keys()) if transcript_data else 'None'}")
        logger.debug(f"Transcript sample first segment: {transcript_data[0] if transcript_data else 'None'}")
        logger.debug(f"Transcript first 500 chars: {full_text_check[:500] if full_text_check else 'Empty'}")

        return {
            "transcript_data": transcript_data,
            "video_id": video_id
        }

    def _get_video_duration_seconds(self, video_id: str) -> Optional[float]:
        """Fetch YouTube video duration in seconds using yt_dlp without downloading the video."""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
            duration = info.get('duration')
            if duration is None:
                logger.warning(f"No duration field found for YouTube video {video_id}")
                return None
            logger.info(f"YouTube video {video_id} duration: {duration} seconds")
            return float(duration)
        except Exception as e:
            logger.warning(f"Failed to fetch duration for YouTube video {video_id}: {e}")
            return None
    
    async def _get_youtube_transcript(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """Attempt to get transcript via YouTube API."""
        if not YouTubeTranscriptApi:
            logger.warning("YouTube Transcript API not available")
            return None

        try:
            # Prefer the simpler API which supports language fallbacks.
            languages = [target_lang_code] if target_lang_code == 'en' else [target_lang_code, 'en']
            logger.info(f"Attempting transcript fetch for {video_id} with languages={languages}")
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)

            # Adapt YouTube API objects to dicts to match PDF standard
            adapted_transcript_data = []
            for entry in transcript_data:
                # youtube_transcript_api commonly returns dicts like:
                # {"text": "...", "start": 12.34, "duration": 4.56}
                # but some implementations may return lightweight objects. Support both.
                if isinstance(entry, dict):
                    start = entry.get('start', 0)
                    duration = entry.get('duration', 0)
                    text = entry.get('text', '')
                else:
                    start = getattr(entry, 'start', 0)
                    duration = getattr(entry, 'duration', 0)
                    text = getattr(entry, 'text', '')

                adapted_transcript_data.append({
                    'start': start,
                    'duration': duration,
                    'text': text,
                })

            # Log YouTube API transcript format (DEBUG only)
            logger.debug(f"YouTube API transcript for video {video_id}: {len(adapted_transcript_data)} segments")
            logger.debug(f"YouTube API transcript first segment: {adapted_transcript_data[0] if adapted_transcript_data else 'None'}")

            logger.info(f"Successfully fetched transcript for {video_id}")
            return adapted_transcript_data
        except Exception as primary_fetch_error:
            logger.warning(f"Transcript fetch failed for {video_id}: {primary_fetch_error}")

        # Fallback path for older transcript APIs or edge cases.
        try:
            logger.info(f"Attempting list_transcripts fallback for {video_id} in language: {target_lang_code}")
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript([target_lang_code])
            transcript_data = transcript.fetch()

            adapted_transcript_data = []
            for entry in transcript_data:
                if isinstance(entry, dict):
                    start = entry.get('start', 0)
                    duration = entry.get('duration', 0)
                    text = entry.get('text', '')
                else:
                    start = getattr(entry, 'start', 0)
                    duration = getattr(entry, 'duration', 0)
                    text = getattr(entry, 'text', '')

                adapted_transcript_data.append({
                    'start': start,
                    'duration': duration,
                    'text': text,
                })

            logger.info(f"Successfully fetched transcript via list_transcripts for {video_id}")
            return adapted_transcript_data
        except Exception as fallback_error:
            logger.warning(f"Transcript list_transcripts fallback failed for {video_id}: {fallback_error}")
            return None
    
    async def _transcribe_with_whisper(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """Use Whisper for local transcription when YouTube API fails."""
        try:
            from api.tasks import download_youtube_audio_segment, transcribe_audio_chunked, adapt_whisper_segments_to_transcript_data
        except ImportError as e:
            logger.error(f"Whisper dependencies not available: {e}")
            return None
            
        temp_audio_file_path = None
        try:
            logger.info(f"Attempting fallback to local Whisper transcription for video ID: {video_id}")
            temp_audio_file_path = download_youtube_audio_segment(video_id, language_code_for_whisper=target_lang_code)
            if not temp_audio_file_path:
                logger.error(f"Failed to download audio for video ID {video_id}")
                return None
                
            logger.info(f"Successfully downloaded audio to {temp_audio_file_path}")
            whisper_lang_code = target_lang_code if target_lang_code and target_lang_code != 'en' else None
            
            try:
                raw_whisper_segments, _ = await asyncio.wait_for(
                    transcribe_audio_chunked(temp_audio_file_path, language=whisper_lang_code),
                    timeout=300  # 5 minutes timeout to match transcription function
                )
                if raw_whisper_segments:
                    transcript_data = adapt_whisper_segments_to_transcript_data(raw_whisper_segments)

                    # Log raw Whisper segments before adaptation (DEBUG only)
                    logger.debug(f"Raw Whisper segments for video {video_id}: {len(raw_whisper_segments)}")
                    logger.debug(f"Raw Whisper first segment: {raw_whisper_segments[0] if raw_whisper_segments else 'None'}")
                    logger.debug(f"Raw Whisper segment keys: {list(raw_whisper_segments[0].keys()) if raw_whisper_segments else 'None'}")

                    logger.info(f"Successfully transcribed using Whisper with {len(raw_whisper_segments)} segments")
                    return transcript_data
                else:
                    logger.error(f"Whisper transcription returned no segments for {video_id}")
                    return None
            except asyncio.TimeoutError:
                logger.error(f"Whisper transcription timed out after 5 minutes for {video_id}")
                return None
            except Exception as whisper_error:
                logger.error(f"Error during Whisper transcription: {whisper_error}", exc_info=True)
                return None
                
        finally:
            if temp_audio_file_path and os.path.exists(temp_audio_file_path):
                try:
                    os.remove(temp_audio_file_path)
                    logger.info(f"Cleaned up temporary audio file: {temp_audio_file_path}")
                except Exception as cleanup_error:
                    logger.error(f"Error cleaning up temp file: {cleanup_error}")
    
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Generate embeddings for full transcript, aligned with PDF processing."""
        transcript_data = content.get('transcript_data', [])
        if not transcript_data:
            raise ValueError("Cannot generate embeddings: No transcript data provided")

        # Build full transcript text
        full_text = '\n'.join([entry['text'] for entry in transcript_data])
        if not full_text.strip():
            raise ValueError("Cannot generate embeddings: Empty transcript")

        # Log chunking summary
        logger.info(f"Chunking transcript for video {content.get('video_id', 'unknown')} ({len(full_text)} chars)")
        logger.debug(f"First 300 chars: {full_text[:300] if full_text else 'Empty'}")

        # Chunk full transcript semantically (similar to PDF)
        try:
            from api.utils import chunk_text  # Explicit import for safety
            chunked_text = chunk_text(full_text, content_type="youtube", target_chunk_tokens=200)
            if not chunked_text:
                raise ValueError("Chunking produced no chunks")
        except Exception as e:
            logger.error(f"Failed to chunk text: {e}", exc_info=True)
            raise ValueError(f"Chunking failed: {str(e)}")
        logger.info(f"Created {len(chunked_text)} chunks from transcript")
        logger.debug(f"Chunk types: {set(type(chunk) for chunk in chunked_text) if chunked_text else 'None'}")

        # Log each chunk details at DEBUG level
        for i, chunk in enumerate(chunked_text[:5]):
            logger.debug(f"Chunk {i+1}: {len(chunk.get('content', ''))} chars - {chunk.get('content', '')[:100]}...")

        # Generate embeddings in batch with validation
        all_small_chunk_texts = [chunk['content'] for chunk in chunked_text]
        try:
            all_embeddings = get_text_embeddings_in_batches(all_small_chunk_texts) if all_small_chunk_texts else []
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise ValueError(f"Failed to generate embeddings: {e}")
        
        # Validate embeddings
        if not all_embeddings or len(all_embeddings) != len(all_small_chunk_texts):
            raise ValueError(f"Embedding count mismatch: expected {len(all_small_chunk_texts)}, got {len(all_embeddings)}")
        
        embedding_dim = len(all_embeddings[0]) if all_embeddings else 0
        if embedding_dim == 0:
            raise ValueError("Embeddings have zero dimensions - model failed to generate valid vectors")
        
        logger.info(f"✅ Generated {len(all_embeddings)} embeddings with dimension {embedding_dim}")

        # Attach embeddings and add timestamp metadata based on transcript segments
        if chunked_text and transcript_data:
            # Create mapping of text positions to timestamps
            cumulative_pos = 0
            pos_to_timestamp = {}
            for segment in transcript_data:
                start_time = segment['start']
                end_time = segment['end'] if 'end' in segment else start_time + segment['duration']
                segment_text = segment['text']
                start_pos = cumulative_pos
                end_pos = start_pos + len(segment_text)
                pos_to_timestamp[start_pos] = (start_time, end_time)
                cumulative_pos = end_pos + 1  # +1 for space/newline

            # Assign timestamps to chunks based on their position in the full text
            for chunk in chunked_text:
                chunk_text_content = chunk['content']
                chunk_start_pos = full_text.find(chunk_text_content[:50])  # Find approximate position
                if chunk_start_pos != -1:
                    # Find the closest segment timestamp
                    best_start = 0
                    best_end = 0
                    min_distance = float('inf')
                    for pos, (start, end) in pos_to_timestamp.items():
                        distance = abs(pos - chunk_start_pos)
                        if distance < min_distance:
                            min_distance = distance
                            best_start, best_end = start, end

                    chunk['metadata'] = {
                        'doc_type': 'youtube',
                        'start_time': best_start,
                        'end_time': best_end,
                        'timestamp': f"{int(best_start//60):02d}:{int(best_start%60):02d} - {int(best_end//60):02d}:{int(best_end%60):02d}"
                    }
                else:
                    chunk['metadata'] = {'doc_type': 'youtube'}

        # Attach embeddings to chunks
        for chunk, embedding in zip(chunked_text, all_embeddings):
            chunk['embedding'] = embedding

        logger.info(f"✅ FINAL CHUNKS WITH EMBEDDINGS: {len(chunked_text)} chunks ready for storage")

        # CRITICAL: Clean up large objects to free memory
        all_small_chunk_texts = None
        all_embeddings = None
        transcript_data = None
        full_text = None
        gc.collect()
        logger.debug("Memory cleaned after embedding generation")

        return {
            "video_id": content.get("video_id"),
            "chunks": chunked_text  # Use the modified chunked_text
        }
        
    async def _store_video_segments(self, user_id: str, file_id: str, filename: str, processed_content: Dict[str, Any]) -> None:
        """Store chunks in the database, aligned with PDF processing."""
        chunks = processed_content.get('chunks', [])
        if not chunks:
            logger.warning(f"No chunks to store for file_id: {file_id}")
            return
        
        logger.info(f"Storing {len(chunks)} chunks for file_id: {file_id}")
        
        # Prepare extracted_data for update_file_with_chunks.
        # AsyncFileRepository.update_file_with_chunks expects a list of segment dicts:
        # { content, page_number, <meta...>, chunks: [{content, embedding}, ...] }
        extracted_data: List[Dict[str, Any]] = []

        for chunk in chunks:
            content = chunk.get('content', '')
            embedding = chunk.get('embedding')
            meta = chunk.get('metadata') or {}

            # Persist video timestamps in Segment.meta_data so SyntextAgent can create citations.
            start_time = meta.get('start_time')
            end_time = meta.get('end_time')

            segment_dict: Dict[str, Any] = {
                'content': content,
                'page_number': None,
                'type': 'video',
                'start_time': start_time,
                'end_time': end_time,
                'chunks': [
                    {
                        'content': content,
                        'embedding': embedding,
                    }
                ],
            }
            extracted_data.append(segment_dict)
        
        success = await self.store.file_repo.update_file_with_chunks(
            user_id=int(user_id),
            filename=filename,
            file_type="youtube",
            extracted_data=extracted_data,
            file_id=int(file_id),
        )
        
        if success:
            logger.info(f"Successfully stored all chunks for file_id: {file_id}")
        else:
            logger.error(f"Failed to store chunks for file_id: {file_id}")
    
    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """Generate key concepts from the YouTube transcript."""
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        transcript_data = content.get('transcript_data', [])
        
        if not transcript_data:
            logger.warning("No transcript data available to generate key concepts")
            return []
        
        # Extract key concepts from full transcript.
        # Include explicit timestamp markers so the LLM can return
        # `source_video_timestamp_start_seconds` / `source_video_timestamp_end_seconds`.
        marked_lines = []
        for entry in transcript_data:
            start_sec = entry.get('start', 0) or 0
            if 'end' in entry and entry.get('end') is not None:
                end_sec = entry.get('end')
            else:
                end_sec = (entry.get('start', 0) or 0) + (entry.get('duration', 0) or 0)

            try:
                start_i = int(float(start_sec))
            except Exception:
                start_i = 0
            try:
                end_i = int(float(end_sec))
            except Exception:
                end_i = start_i

            marked_lines.append(f"[{start_i}-{end_i}] {entry.get('text', '')}")

        full_text = "\n".join(marked_lines)
        if not full_text.strip():
            logger.warning("No transcript available to generate key concepts")
            return []

        logger.info(f"Generating key concepts for video {content.get('video_id', 'unknown')} ({len(full_text)} chars, {language}, {comprehension_level})")

        try:
            key_concepts = generate_key_concepts(document_text=full_text, language=language, comprehension_level=comprehension_level, is_video=True)

            # Enforce an upper bound on number of concepts for long videos
            if key_concepts and isinstance(key_concepts, list) and len(key_concepts) > MAX_CONCEPTS_PER_VIDEO:
                logger.info(
                    f"Truncating key concepts list from {len(key_concepts)} to {MAX_CONCEPTS_PER_VIDEO} for video {content.get('video_id', 'unknown')}"
                )
                key_concepts = key_concepts[:MAX_CONCEPTS_PER_VIDEO]

            # Assign timestamps based on transcript segments (similar to page numbers in PDF)
            if transcript_data and key_concepts:
                # Create a mapping of text positions to timestamps
                cumulative_text = ""
                text_to_timestamp = {}
                for segment in transcript_data:
                    start_time = segment['start']
                    end_time = segment['end'] if 'end' in segment else start_time + segment['duration']
                    segment_text = segment['text']
                    start_pos = len(cumulative_text)
                    end_pos = start_pos + len(segment_text)
                    text_to_timestamp[start_pos] = (start_time, end_time, segment_text)
                    cumulative_text += segment_text + " "

                # Assign timestamps to concepts based on where they appear in the text
                for concept in key_concepts:
                    # Find the concept in the full text (simple substring match)
                    concept_start = full_text.lower().find(concept.get('concept_title', '').lower())
                    if concept_start == -1:
                        concept_start = full_text.lower().find(concept.get('concept_explanation', '').lower()[:50])

                    if concept_start != -1:
                        # Find the closest segment timestamp
                        best_match = None
                        min_distance = float('inf')
                        for pos, (start, end, text) in text_to_timestamp.items():
                            distance = abs(pos - concept_start)
                            if distance < min_distance:
                                min_distance = distance
                                best_match = (start, end)

                        if best_match:
                            start_sec, end_sec = best_match
                            concept['source_video_timestamp_start_seconds'] = start_sec
                            concept['source_video_timestamp_end_seconds'] = end_sec
                            concept['source_video_timestamp'] = f"{int(start_sec//60):02d}:{int(start_sec%60):02d} - {int(end_sec//60):02d}:{int(end_sec%60):02d}"

            logger.info(f"Generated {len(key_concepts)} key concepts from YouTube transcript")
            for i, concept in enumerate(key_concepts[:3]):
                logger.debug(f"Concept {i+1}: {concept.get('concept_title', 'No title')[:50]} at {concept.get('source_video_timestamp', 'N/A')}")
            return key_concepts
        except Exception as e:
            self._log_error("Error generating key concepts", e)
            return []
    
    async def generate_learning_materials_for_concept(self, file_id: str, concept: Dict[str, Any]) -> bool:
        """Generate and save learning materials for a single key concept."""
        try:
            return await generate_learning_materials_for_concept(self.store, int(file_id), concept)
        except Exception as e:
            logger.error(f"Error generating learning materials: {e}", exc_info=True)
            return False
            
    async def generate_learning_materials(self, file_id: str, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate flashcards, MCQs, and T/F questions from key concepts."""
        if not key_concepts:
            logger.warning(f"No key concepts provided to generate learning materials for file {file_id}")
            return {"concepts_processed": 0, "concepts_successful": 0, "concepts_failed": 0}
            
        concept_results = []
        for concept in key_concepts:
            result = await self.generate_learning_materials_for_concept(file_id, concept)
            concept_results.append(result)
            
        return await log_concept_processing_summary(concept_results, int(file_id))
        
    def _log_error(self, message: str, error: Exception) -> None:
        """Helper method to log errors with consistent formatting."""
        logger.error(f"{message}: {str(error)[:200]}", exc_info=True)