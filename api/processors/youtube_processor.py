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

logger = logging.getLogger(__name__)

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
            
            # Step 5: Generate learning materials
            if key_concepts:
                await self.generate_learning_materials(file_id, key_concepts)
            
            # Update status
            await self.store.update_file_status_async(int(file_id), "processed")
        
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
            await self.store.update_file_status_async(int(file_id), "error", str(e)[:200])
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
            segment = {
                'start_time': entry['start'],
                'end_time': entry['start'] + entry['duration'],
                'content': entry['text'],
                'duration': entry['duration']
            }
            processed_video_data.append(segment)
            all_small_chunks.append((len(processed_video_data)-1, entry['text']))
            segment_chunk_map[len(processed_video_data)-1] = [entry['text']]
        
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
        Generate key concepts from processed video segments.
        
        Args:
            processed_content: Dict with processed segments
            **kwargs: Additional parameters like language and comprehension_level
            
        Returns:
            List of key concepts
        """
        from ..tasks import get_key_concepts_from_segments_with_timeout
        
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        segments = processed_content.get('processed_segments', [])
        
        if not segments:
            logger.warning("No segments available to generate key concepts.")
            return []
        
        # Extract content from segments for key concept generation
        segment_texts = [segment['content'] for segment in segments]
        full_text = '\n'.join(segment_texts)
        
        # Use a timeout to prevent hanging on LLM calls
        try:
            key_concepts = await asyncio.wait_for(
                get_key_concepts_from_segments_with_timeout(full_text, language, comprehension_level),
                timeout=300  # 5 minutes timeout
            )
            return key_concepts
        except asyncio.TimeoutError:
            logger.error("Key concept generation timed out after 5 minutes")
            return []
        except Exception as e:
            self._log_error("Error generating key concepts", e)
            return []
    
    async def generate_learning_materials(self, file_id: str, key_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate flashcards, MCQs, and T/F questions from key concepts.
        
        Args:
            file_id: File ID
            key_concepts: List of key concepts
            
        Returns:
            Dict with counts of generated materials
        """
        from ..tasks import (
            generate_flashcards_from_key_concepts, 
            generate_mcq_from_key_concepts,
            generate_true_false_from_key_concepts
        )
        
        if not key_concepts:
            logger.warning(f"No key concepts available to generate learning materials for file_id: {file_id}")
            return {"flashcards": 0, "mcqs": 0, "true_false": 0}
        
        results = {"flashcards": 0, "mcqs": 0, "true_false": 0}
        
        # Store key concepts
        concept_ids = []
        for concept in key_concepts:
            # Extract concept information (handle different formats)
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            # Add key concept and store the returned ID
            concept_id = await self.store.add_key_concept_async(
                file_id=int(file_id),
                concept_title=concept_title,
                concept_explanation=concept_explanation,
                source_video_timestamp_start_seconds=concept.get('source_video_timestamp_start_seconds'),
                source_video_timestamp_end_seconds=concept.get('source_video_timestamp_end_seconds')
            )
            if concept_id:
                concept_ids.append(concept_id)
        
        # Generate flashcards with timeout
        try:
            flashcards = await asyncio.wait_for(
                generate_flashcards_from_key_concepts(key_concepts), 
                timeout=180  # 3 minutes timeout
            )
            
            if flashcards:
                logger.info(f"Generated {len(flashcards)} flashcards from key concepts")
                for card in flashcards:
                    try:
                        # Use the first concept ID if available, otherwise None
                        # Ensure we're passing an integer or None, never an empty list
                        key_concept_id = concept_ids[0] if concept_ids else None
                        
                        # Fix parameter order to match repository implementation
                        await self.store.add_flashcard_async(
                            file_id=int(file_id),
                            question=card.get('front', ''),
                            answer=card.get('back', ''),
                            key_concept_id=None if key_concept_id is None else int(key_concept_id),
                            is_custom=False
                        )
                    except Exception as e:
                        self._log_error(f"Error adding flashcard: {e}", e)
                results["flashcards"] = len(flashcards)
        except (asyncio.TimeoutError, Exception) as e:
            self._log_error("Error generating flashcards", e)
        
        # Generate MCQs with timeout
        try:
            mcqs = await asyncio.wait_for(
                generate_mcq_from_key_concepts(key_concepts),
                timeout=180  # 3 minutes timeout
            )
            
            if mcqs:
                logger.info(f"Generated {len(mcqs)} MCQs from key concepts")
                for mcq in mcqs:
                    # Extract the correct answer and distractors from options
                    options = mcq.get('options', [])
                    answer = mcq.get('answer', '')
                    
                    # Format data for the updated method signature
                    try:
                        # Use the first concept ID if available, otherwise None
                        # Ensure we're passing an integer or None, never an empty list
                        key_concept_id = concept_ids[0] if concept_ids else None
                        
                        # Fix parameter order to match repository implementation
                        await self.store.add_quiz_question_async(
                            file_id=int(file_id),
                            question=mcq.get('question', ''),
                            question_type="MCQ",  # Consistent capitalization
                            correct_answer=answer,
                            distractors=[opt for opt in options if opt != answer],
                            key_concept_id=None if key_concept_id is None else int(key_concept_id)  # Pass as keyword arg
                        )
                    except Exception as e:
                        self._log_error(f"Error adding MCQ question: {e}", e)
                results["mcqs"] = len(mcqs)
        except (asyncio.TimeoutError, Exception) as e:
            self._log_error("Error generating MCQs", e)
        
        # Generate True/False questions with timeout
        try:
            tf_questions = await asyncio.wait_for(
                generate_true_false_from_key_concepts(key_concepts),
                timeout=180  # 3 minutes timeout
            )
            
            if tf_questions:
                logger.info(f"Generated {len(tf_questions)} True/False questions from key concepts")
                for tf in tf_questions:
                    # Create a properly formatted True/False question
                    try:
                        # Use the first concept ID if available, otherwise None
                        # Ensure we're passing an integer or None, never an empty list
                        key_concept_id = concept_ids[0] if concept_ids else None
                        
                        # Make sure the key_concept_id is an integer or None
                        # This prevents empty list being passed when concept_ids is empty
                        await self.store.add_quiz_question_async(
                            file_id=int(file_id),
                            question=tf.get('statement', ''),
                            question_type="TF",  # Consistent type identifier
                            correct_answer="True" if tf.get('is_true', False) else "False",
                            distractors=None,  # T/F questions don't need distractors, use None instead of empty list
                            key_concept_id=None if key_concept_id is None else int(key_concept_id)  # Pass as keyword arg
                        )
                    except Exception as e:
                        self._log_error(f"Error adding True/False question: {e}", e)
                results["true_false"] = len(tf_questions)
        except (asyncio.TimeoutError, Exception) as e:
            self._log_error("Error generating True/False questions", e)
        
        return results
        
    def _log_error(self, message: str, error: Exception) -> None:
        """
        Helper method to log errors with consistent formatting.
        
        Args:
            message: Error message context
            error: Exception object
        """
        logger.error(f"{message}: {str(error)[:200]}", exc_info=True)
