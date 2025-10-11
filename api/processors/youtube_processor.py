"""
YouTube processor module - Handles extraction and processing of YouTube videos.
"""
import asyncio
import logging
import os
import re
from typing import Dict, List, Any, Optional, Tuple

# Use absolute imports instead of relative imports
from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.processors.processor_utils import generate_learning_materials_for_concept, log_concept_processing_summary
from api.llm_service import generate_key_concepts_dspy, get_text_embeddings_in_batches
from api.schemas.learning_content import KeyConceptCreate

# Import required only for YouTube processing
try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
except ImportError:
    logger.warning("YouTube Transcript API not available. YouTube processing will be limited.")

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
        user_gc_id = kwargs.get('user_gc_id', '')
        
        try:
            # Step 1: Extract content (transcript)
            content = await self.extract_content(
                filename=filename, 
                language=language
            )
            
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
                    
                    # Save the concept to get its ID
                    key_concept_create = KeyConceptCreate(
                        concept_title=title,
                        concept_explanation=explanation,
                        source_page_number=concept.get("source_page_number"),
                        source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                        source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds"),
                        is_custom=False
                    )
                    concept_result = await self.store.learning_material_repo.add_key_concept(
                        file_id=int(file_id),
                        key_concept_data=key_concept_create
                    )
                    concept_id = concept_result.get('id') if concept_result else None
                    
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
            
        # Extract video ID from URL
        yt_match = re.search(r'(?:v=|youtu.be/|embed/)([\w-]{11})', filename)
        video_id = yt_match.group(1) if yt_match else None
        
        if not video_id:
            raise ValueError(f"Could not extract video ID from YouTube URL: {filename}")
            
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
    
    async def _get_youtube_transcript(self, video_id: str, target_lang_code: str) -> List[Dict[str, Any]]:
        """
        Attempt to get transcript via YouTube API.
        
        Args:
            video_id: YouTube video ID
            target_lang_code: Language code for transcript
            
        Returns:
            List of transcript segments or None if failed
        """
        try:
            # First attempt: direct fetch with requested language
            logger.info(f"Attempting direct transcript fetch for {video_id} in language: {target_lang_code}")
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = transcript_list.find_transcript([target_lang_code])
            transcript_data = transcript.fetch()
            logger.info(f"Successfully fetched transcript in {target_lang_code}")
            return transcript_data
        except Exception as direct_fetch_error:
            logger.warning(f"Direct transcript fetch failed: {direct_fetch_error}")
            
            # Second attempt: try English if the requested language wasn't English
            if target_lang_code != 'en':
                try:
                    logger.info(f"Attempting fallback to English transcript for {video_id}")
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id) 
                    transcript = transcript_list.find_transcript(['en'])
                    transcript_data = transcript.fetch()
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
            target_lang_code: Language code for transcript
            
        Returns:
            List of transcript segments or None if failed
        """
        from ..tasks import download_youtube_audio_segment, transcribe_audio_chunked, adapt_whisper_segments_to_transcript_data
        
        temp_audio_file_path = None
        try:
            logger.info(f"Attempting fallback to local Whisper transcription for video ID: {video_id}")
            
            # Download YouTube audio
            temp_audio_file_path = download_youtube_audio_segment(video_id, language_code_for_whisper=target_lang_code)
            if not temp_audio_file_path:
                logger.error(f"Failed to download audio for video ID {video_id}.")
                return None
            
            logger.info(f"Successfully downloaded audio to {temp_audio_file_path} for Whisper processing.")
            
            # Determine language for Whisper
            whisper_lang_code = target_lang_code if target_lang_code and target_lang_code != 'en' else None
            
            # Add timeout to prevent hanging indefinitely
            try:
                raw_whisper_segments, _ = await asyncio.wait_for(
                    transcribe_audio_chunked(temp_audio_file_path, language=whisper_lang_code),
                    timeout=600  # 10 minute timeout
                )
                
                if raw_whisper_segments:
                    transcript_data = adapt_whisper_segments_to_transcript_data(raw_whisper_segments)
                    logger.info(f"Successfully transcribed using Whisper with {len(raw_whisper_segments)} segments")
                    return transcript_data
                else:
                    logger.error(f"Whisper transcription returned no segments for {video_id}.")
                    return None
                    
            except asyncio.TimeoutError:
                logger.error(f"Whisper transcription timed out after 10 minutes for {video_id}")
                return None
                
            except Exception as whisper_error:
                logger.error(f"Error during Whisper transcription: {whisper_error}", exc_info=True)
                return None
                
        except Exception as whisper_fallback_error:
            logger.error(f"Error in Whisper fallback process: {whisper_fallback_error}", exc_info=True)
            return None
            
        finally:
            # Clean up temp files
            if temp_audio_file_path and os.path.exists(temp_audio_file_path):
                try:
                    os.remove(temp_audio_file_path)
                    logger.info(f"Cleaned up temporary audio file: {temp_audio_file_path}")
                except Exception as cleanup_error:
                    logger.error(f"Error cleaning up temp file: {cleanup_error}")
                    # Continue processing even if cleanup fails
                    
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for transcript segments.
        
        Args:
            content: Dict containing transcript data
            
        Returns:
            Dict with processed segments including embeddings
        """
        from ..tasks import get_text_embeddings_in_batches
        
        transcript_data = content.get('transcript_data', [])
        if not transcript_data:
            raise ValueError("Cannot generate embeddings: No transcript data provided")
        
        # Process transcript into segments with chunks
        processed_video_data = []
        all_small_chunks = []  # List to hold tuples of (segment_index, chunk_text)
        segment_chunk_map = {}  # Map segment index to its list of small chunk texts
        
        for entry in transcript_data:
            # Handle both dictionary-style entries and object-style entries (FetchedTranscriptSnippet)
            try:
                # Try dictionary access first
                start = entry['start']
                duration = entry['duration']
                text = entry['text']
            except (TypeError, KeyError):
                # If that fails, try attribute access
                start = entry.start
                duration = entry.duration
                text = entry.text
                
            segment = {
                'start_time': start,
                'end_time': start + duration,
                'content': text,
                'duration': duration
            }
            processed_video_data.append(segment)
            all_small_chunks.append((len(processed_video_data)-1, text))
            segment_chunk_map[len(processed_video_data)-1] = [text]
        
        # Generate embeddings for all small chunks across the video in one batch
        logger.info(f"Generating embeddings for {len(all_small_chunks)} small chunks across video...")
        all_small_chunk_texts = [text for _, text in all_small_chunks]
        
        all_embeddings = []
        if all_small_chunk_texts:
            all_embeddings = get_text_embeddings_in_batches(all_small_chunk_texts)
            
        # Create a mapping from chunk text to its embedding
        embedding_dict = {}
        if len(all_embeddings) == len(all_small_chunks):
            embedding_dict = {all_small_chunks[i][1]: all_embeddings[i] for i in range(len(all_small_chunks))}
        
        # Attach embeddings to segments
        for i, segment in enumerate(processed_video_data):
            segment_chunks = segment_chunk_map.get(i, [])
            segment['chunks'] = []
            for small_chunk_text in segment_chunks:
                embedding = embedding_dict.get(small_chunk_text)  # Look up embedding
                segment['chunks'].append({
                    'embedding': embedding,
                    'content': small_chunk_text
                })
        
        return {
            "video_id": content.get("video_id"),
            "processed_segments": processed_video_data
        }
        
    async def _store_video_segments(self, user_id: str, file_id: str, filename: str, processed_content: Dict[str, Any]) -> None:
        """
        Store processed segments and chunks in the database.
        
        Args:
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
        success = await self.store.file_repo.update_file_with_chunks(
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
        # Use the generate_key_concepts_dspy function already imported at the top of the file
        
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        segments = processed_content.get('processed_segments', [])
        
        if not segments:
            logger.warning("No segments available to generate key concepts.")
            return []
        
        # Extract content from segments for key concept generation
        segment_texts = [segment['content'] for segment in segments]
        full_text = '\n'.join(segment_texts)
        
        try:
            # Use the same function as PDFProcessor
            key_concepts = generate_key_concepts_dspy(
                document_text=full_text,
                language=language,
                comprehension_level=comprehension_level
            )
            logger.info(f"Generated {len(key_concepts)} key concepts from YouTube transcript")
            return key_concepts
        except Exception as e:
            self._log_error("Error generating key concepts", e)
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
