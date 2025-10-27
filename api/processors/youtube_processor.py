import asyncio
import logging
import os
import re
from typing import Dict, List, Any, Optional, Tuple
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.processors.processor_utils import generate_learning_materials_for_concept, log_concept_processing_summary
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
            await self._store_video_segments(user_id, file_id, filename, processed_content)

            # Step 4: Generate key concepts
            key_concepts = await self.generate_key_concepts(
                content,
                language=language,
                comprehension_level=comprehension_level
            )

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
            concepts_processed = 0
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
                    # Save concept within transaction
                    async with self.store.file_repo.get_async_session() as concept_transaction:
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
                            concept_with_id = {
                                "concept_title": title,
                                "concept_explanation": explanation,
                                "id": concept_id
                            }
                            result = await self.generate_learning_materials_for_concept(file_id, concept_with_id)
                            if result:
                                concepts_processed += 1
                            else:
                                logger.error(f"Failed to generate learning materials for concept '{title[:30]}...'")
                        else:
                            logger.error(f"Failed to save concept '{title[:30]}...' to database")
                except Exception as e:
                    logger.error(f"Error processing concept '{title[:30]}...': {e}")
                    continue

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
            
        target_lang_code = LANGUAGE_CODE_MAP.get(language.lower(), 'en')
        
        transcript_data = await self._transcribe_with_whisper(video_id, target_lang_code)
        if not transcript_data:
            logger.warning(f"Whisper transcription failed for {video_id}, falling back to YouTube API")
            transcript_data = await self._get_youtube_transcript(video_id, target_lang_code)
            if not transcript_data:
                raise ValueError(f"Failed to extract transcript from video {video_id}")
            else:
                # Check if YouTube transcript has actual text
                full_text_check = '\n'.join([entry['text'] for entry in transcript_data])
                if not full_text_check.strip():
                    logger.warning(f"YouTube transcript is empty for {video_id}, using anyway as last resort")
        else:
            # Check if Whisper transcript has actual text
            full_text_check = '\n'.join([entry['text'] for entry in transcript_data])
            if not full_text_check.strip():
                logger.warning(f"Whisper transcript is empty for {video_id}, falling back to YouTube API")
                transcript_data = await self._get_youtube_transcript(video_id, target_lang_code)
                if not transcript_data:
                    raise ValueError(f"Failed to extract transcript from video {video_id}")
                else:
                    full_text_check = '\n'.join([entry['text'] for entry in transcript_data])
                    if not full_text_check.strip():
                        logger.warning(f"YouTube transcript is also empty for {video_id}")
                        raise ValueError(f"Failed to extract transcript from video {video_id}")

        # Calculate full text for logging
        full_text_check = '\n'.join([entry['text'] for entry in transcript_data])

        # Log transcript data details
        logger.info(f"📝 TRANSCRIPT DATA RECEIVED for video {video_id}:")
        logger.info(f"   - Number of segments: {len(transcript_data)}")
        logger.info(f"   - First segment type: {type(transcript_data[0]) if transcript_data else 'None'}")
        logger.info(f"   - First segment keys: {list(transcript_data[0].keys()) if transcript_data else 'None'}")
        logger.info(f"   - Sample first segment: {transcript_data[0] if transcript_data else 'None'}")
        logger.info(f"   - Full transcript length: {len(full_text_check)} characters")
        logger.info(f"   - First 500 chars: {full_text_check[:500] if full_text_check else 'Empty'}")

        return {
            "transcript_data": transcript_data,
            "video_id": video_id
        }
    
    async def _get_youtube_transcript(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """Attempt to get transcript via YouTube API."""
        if not YouTubeTranscriptApi:
            logger.warning("YouTube Transcript API not available")
            return None
            
        try:
            logger.info(f"Attempting direct transcript fetch for {video_id} in language: {target_lang_code}")
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript([target_lang_code])
            transcript_data = transcript.fetch()

            # Adapt YouTube API objects to dicts to match PDF standard
            adapted_transcript_data = []
            for entry in transcript_data:
                adapted_transcript_data.append({
                    'start': entry.start,
                    'duration': entry.duration,
                    'text': entry.text
                })

            # Log YouTube API transcript format
            logger.info(f"🎬 YOUTUBE API TRANSCRIPT for video {video_id}:")
            logger.info(f"   - Number of segments: {len(adapted_transcript_data)}")
            logger.info(f"   - First segment: {adapted_transcript_data[0] if adapted_transcript_data else 'None'}")

            logger.info(f"Successfully fetched transcript in {target_lang_code}")
            return adapted_transcript_data
        except Exception as direct_fetch_error:
            logger.warning(f"Direct transcript fetch failed: {direct_fetch_error}")
            if target_lang_code != 'en':
                try:
                    logger.info(f"Attempting fallback to English transcript for {video_id}")
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    transcript = transcript_list.find_transcript(['en'])
                    transcript_data = transcript.fetch()

                    # Adapt English fallback to dicts
                    adapted_transcript_data = []
                    for entry in transcript_data:
                        adapted_transcript_data.append({
                            'start': entry.start,
                            'duration': entry.duration,
                            'text': entry.text
                        })

                    # Log English fallback transcript format
                    logger.info(f"🇺🇸 ENGLISH FALLBACK TRANSCRIPT for video {video_id}:")
                    logger.info(f"   - Number of segments: {len(adapted_transcript_data)}")
                    logger.info(f"   - First segment: {adapted_transcript_data[0] if adapted_transcript_data else 'None'}")

                    logger.info("Successfully fetched transcript in English as fallback")
                    return adapted_transcript_data
                except Exception as english_fetch_error:
                    logger.warning(f"English transcript fallback also failed: {english_fetch_error}")
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

                    # Log raw Whisper segments before adaptation
                    logger.info(f"🔊 RAW WHISPER SEGMENTS for video {video_id}:")
                    logger.info(f"   - Number of raw segments: {len(raw_whisper_segments)}")
                    logger.info(f"   - First raw segment: {raw_whisper_segments[0] if raw_whisper_segments else 'None'}")
                    logger.info(f"   - Raw segment keys: {list(raw_whisper_segments[0].keys()) if raw_whisper_segments else 'None'}")

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

        # Log chunking input
        logger.info(f"🔄 CHUNKING PROCESS for video {content.get('video_id', 'unknown')}:")
        logger.info(f"   - Full transcript length: {len(full_text)} characters")
        logger.info(f"   - First 300 chars: {full_text[:300] if full_text else 'Empty'}")

        # Chunk full transcript semantically (similar to PDF)
        try:
            from api.utils import chunk_text  # Explicit import for safety
            chunked_text = chunk_text(full_text, content_type="youtube", target_chunk_tokens=200)
            if not chunked_text:
                raise ValueError("Chunking produced no chunks")
        except Exception as e:
            logger.error(f"Failed to chunk text: {e}", exc_info=True)
            raise ValueError(f"Chunking failed: {str(e)}")
        logger.info(f"📦 CHUNKING RESULTS:")
        logger.info(f"   - Number of chunks created: {len(chunked_text)}")
        logger.info(f"   - Chunk types: {set(type(chunk) for chunk in chunked_text) if chunked_text else 'None'}")

        # Log each chunk details
        for i, chunk in enumerate(chunked_text[:5]):  # Log first 5 chunks
            logger.info(f"   - Chunk {i+1}: {len(chunk.get('content', ''))} chars")
            logger.info(f"     Content: {chunk.get('content', '')[:200]}...")

        if len(chunked_text) > 5:
            logger.info(f"   - ... and {len(chunked_text) - 5} more chunks")

        # Generate embeddings in batch
        all_small_chunk_texts = [chunk['content'] for chunk in chunked_text]
        all_embeddings = get_text_embeddings_in_batches(all_small_chunk_texts) if all_small_chunk_texts else []
        logger.info(f"🧮 EMBEDDING RESULTS:")
        logger.info(f"   - Number of embeddings generated: {len(all_embeddings)}")
        logger.info(f"   - Embedding dimensions: {len(all_embeddings[0]) if all_embeddings else 'None'}")

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
        
        # Prepare extracted_data for update_file_with_chunks (similar to PDF)
        extracted_data = []
        for chunk in chunks:
            extracted_data.append({
                'text': chunk['content'],  # Use 'text' for ChunkORM
                'embedding': chunk['embedding'],
                'metadata': chunk['metadata']
            })
        
        success = await self.store.file_repo.update_file_with_chunks(
            user_id=int(user_id),
            filename=filename,
            file_type="youtube",
            extracted_data=extracted_data
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
        
        # Extract key concepts from full transcript
        full_text = '\n'.join([entry['text'] for entry in transcript_data])
        if not full_text.strip():
            logger.warning("No transcript available to generate key concepts")
            return []

        logger.info(f"🧠 GENERATING KEY CONCEPTS for video {content.get('video_id', 'unknown')}:")
        logger.info(f"   - Full text length: {len(full_text)} characters")
        logger.info(f"   - Language: {language}, Comprehension: {comprehension_level}")

        try:
            key_concepts = generate_key_concepts(document_text=full_text, language=language, comprehension_level=comprehension_level, is_video=True)

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

            logger.info(f"✨ KEY CONCEPTS GENERATED:")
            logger.info(f"   - Number of concepts: {len(key_concepts)}")
            for i, concept in enumerate(key_concepts[:3]):  # Log first 3 concepts
                logger.info(f"   - Concept {i+1}: {concept.get('concept_title', 'No title')}")
                logger.info(f"     Explanation: {concept.get('concept_explanation', 'No explanation')[:150]}...")
                if 'source_video_timestamp' in concept:
                    logger.info(f"     Timestamp: {concept['source_video_timestamp']}")

            logger.info(f"Generated {len(key_concepts)} key concepts from YouTube transcript")
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