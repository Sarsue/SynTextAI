import logging
import os
import tempfile
from contextlib import contextmanager
import asyncio
from faster_whisper import WhisperModel
import yt_dlp
from utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from repositories.repository_manager import RepositoryManager
from llm_service import get_text_embeddings_in_batches, get_text_embedding, token_count, MAX_TOKENS_CONTEXT, generate_key_concepts_dspy
from syntext_agent import SyntextAgent
import stripe
from websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import BackgroundTasks, HTTPException
import gc
from typing import Optional

# Load environment variables
load_dotenv()

# Load Whisper model once at startup
whisper_model = None # Initialize as None, will be loaded by load_whisper_model

def load_whisper_model_if_needed():
    global whisper_model
    if whisper_model is None:
        logger.info("Loading faster-whisper model...")
        # Consider making model size/params configurable if needed later
        whisper_model = WhisperModel("medium", device="cpu", compute_type="int8", download_root="/app/models")
        logger.info("Faster-whisper model loaded.")
    return whisper_model

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

# Model is only loaded when needed to save memory
@contextmanager
def load_whisper_model():
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    try:
        yield model
    finally:
        del model  # Explicitly delete to free memory



async def transcribe_audio_chunked(file_path: str, language: str = "en", chunk_duration_ms: int = 30000, overlap_ms: int = 5000) -> tuple[list, any]:
    model = load_whisper_model_if_needed() # Ensure model is loaded
    transcribe_params = {
        'language': language if language else None, # None for auto-detect, or specify lang code
        'beam_size': 5,
        'vad_filter': True,
        'vad_parameters': dict(min_silence_duration_ms=500)
    }
    logger.info(f"Transcribing audio file {file_path} with params: {transcribe_params}")
    try:
        # Run the blocking transcribe call in a separate thread
        segments_generator, info = await asyncio.to_thread(
            model.transcribe, file_path, **transcribe_params
        )
        # Convert generator to list of segments
        processed_segments = []
        for segment in segments_generator:
            processed_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })
        logger.info(f"Transcription successful for {file_path}. Detected language: {info.language}, Confidence: {info.language_probability}")
        return processed_segments, info
    except Exception as e:
        logger.error(f"Error during transcription of {file_path}: {e}", exc_info=True)
        return [], None

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

def adapt_whisper_segments_to_transcript_data(whisper_segments: list) -> list:
    """Converts Whisper segments to the format expected by downstream processing."""
    transcript_data = []
    for segment in whisper_segments:
        start_time = float(segment['start'])
        end_time = float(segment['end'])
        duration = end_time - start_time
        transcript_data.append({
            'start': start_time,
            'duration': duration,
            'text': segment['text']
            # 'end_time' is not directly in this structure but can be derived
        })
    return transcript_data

async def process_file_data(user_gc_id: str, user_id: str, file_id: str, filename: str, file_url: str, is_youtube: bool = False, language: str = "English", comprehension_level: str = "Beginner"):
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate key concepts, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level})")
    
    from .docsynth_store import DocSynthStore
    from .processors.factory import FileProcessingFactory
    
    # Initialize the document store and processor factory
    store = DocSynthStore()
    factory = FileProcessingFactory(store)
    
    # Get the appropriate processor for this file
    processor = factory.get_processor(filename)
    
    # Log which processor was selected (or if falling back to legacy)
    if processor:
        logger.info(f"Using {processor.__class__.__name__} for file: {filename}")
    else:
        logger.info(f"No specialized processor available for file: {filename}, will use legacy processing")
    
    if processor:
        try:
            # Process the file using the selected processor
            result = await processor.process(
                user_id=user_id,
                file_id=file_id,
                filename=filename,
                file_url=file_url,
                user_gc_id=user_gc_id,
                language=language,
                comprehension_level=comprehension_level
            )
            
            if not result.get("success", False):
                logger.error(f"Processing failed: {result.get('error', 'Unknown error')}")
                store.update_file_status(int(file_id), "error", result.get('error', 'Processing failed')[:200])
                return  # Exit without marking as processed so it can be retried
                
            logger.info(f"Processing completed successfully for file_id: {file_id}")
            return
        except Exception as e:
            logger.error(f"Error in file processing: {e}", exc_info=True)
            store.update_file_status(int(file_id), "error", str(e)[:200])
            return
    
    # If we get here, either no processor was found or we need to fall back to legacy processing
    logger.warning(f"No specialized processor found for {filename}, falling back to legacy processing")
    
    # Legacy processing code follows - only executed when no processor is available from the factory
    # Format timestamps in the transcript to help LLM identify segments
    def format_timestamp(seconds):
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    
    # Add timestamps to each segment
    full_transcription_for_concepts = ""
    for segment in processed_video_data:
        if segment.get('content', '').strip():
            start_time = segment.get('start_time', 0)
            end_time = segment.get('end_time', 0)
            timestamp_text = f"[{format_timestamp(start_time)} - {format_timestamp(end_time)}] "
            full_transcription_for_concepts += timestamp_text + segment.get('content', '') + "\n\n"
    
    logger.info(f"Prepared transcript with timestamps for LLM processing, first 500 chars: {full_transcription_for_concepts[:500]}...")
    
    logger.info(f"YouTube transcript length for key concepts: {len(full_transcription_for_concepts)} characters for file ID: {file_id}")
    
    if not full_transcription_for_concepts or len(full_transcription_for_concepts.strip()) < 50:
        logger.error(f"YouTube transcript too short or empty for key concept extraction. File ID: {file_id}")
        # Still mark the file as processed to prevent infinite retry loop
        store.update_file_processing_status(int(file_id), True) 
        return

    # Attempt to generate and store key concepts with robust error handling
    try:
        logger.info(f"Generating key concepts for YouTube video: {filename} (File ID: {file_id})")
        
        # Log sample of the transcript to help with debugging
        transcript_sample = full_transcription_for_concepts[:500] + "..." if len(full_transcription_for_concepts) > 500 else full_transcription_for_concepts
        logger.info(f"Transcript sample for key concept generation (File ID: {file_id}): {transcript_sample}")
        
        # Set a timeout for LLM generation to avoid indefinite hanging
        import concurrent.futures
    except Exception as sample_error:
        logger.error(f"Error preparing transcript sample: {sample_error}")
        # Continue processing even if logging fails
    
    import threading
    
    key_concepts_list = None
    
    def generate_with_timeout():
        try:
            logger.info(f"Starting LLM concept generation with timeout for file ID: {file_id}")
            result = generate_key_concepts_dspy(
                document_text=full_transcription_for_concepts,
                language=language,
                comprehension_level=comprehension_level,
                is_video=True  # Flag to indicate this is a video
            )
            return result
        except Exception as e:
            logger.error(f"Error in LLM concept generation thread: {e}", exc_info=True)
            return None
    
    # Use a thread with timeout for LLM processing
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(generate_with_timeout)
            try:
                # Set a reasonable timeout (5 minutes)
                key_concepts_list = future.result(timeout=300)
                if key_concepts_list:
                    logger.info(f"LLM returned {len(key_concepts_list)} key concepts for video ID: {file_id}")
                else:
                    logger.warning(f"LLM returned None or empty list for file ID: {file_id}")
                    # Create a minimal set of default concepts to avoid total failure
                    key_concepts_list = [
                        {"concept_title": "Main Topic", "concept_explanation": "The video discusses its main topic. Check the full video for details."}
                    ]
            except concurrent.futures.TimeoutError:
                logger.error(f"LLM concept generation timed out after 5 minutes for file ID: {file_id}")
                # Create a minimal set of default concepts to provide some value
                key_concepts_list = [
                    {"concept_title": "Video Content", "concept_explanation": "This concept could not be automatically extracted due to a timeout. Please view the video directly."}
                ]
    except Exception as executor_error:
        logger.error(f"Error in thread executor: {executor_error}", exc_info=True)
        key_concepts_list = [
            {"concept_title": "Video Content", "concept_explanation": "Unable to analyze this video content due to a processing error."}
        ]
    
    # Try to process the key concepts with proper error handling
    try:
        # Proceed only if we have concepts
        if key_concepts_list and len(key_concepts_list) > 0:
            # Use the file_id directly
            db_file_id = int(file_id)
            
            # The LLM should have extracted timestamps directly from the formatted transcript
            logger.info(f"Verifying timestamps in {len(key_concepts_list)} key concepts generated by LLM")
            
            # Log the timestamp information for verification
            for idx, concept in enumerate(key_concepts_list):
                start = concept.get('source_video_timestamp_start_seconds')
                end = concept.get('source_video_timestamp_end_seconds')
                if start is not None and end is not None:
                    logger.info(f"Concept {idx+1}: '{concept.get('concept_title', '')[:30]}...' has timestamps {start}s - {end}s")
                else:
                    logger.warning(f"Concept {idx+1}: '{concept.get('concept_title', '')[:30]}...' is missing timestamp information")
            
            # Store key concepts with robust error handling
            logger.info(f"Storing {len(key_concepts_list)} key concepts for file ID: {db_file_id}")
            key_concept_db_ids = []
            
            for idx, concept_data in enumerate(key_concepts_list):
                try:
                    # Default values in case the LLM returns incomplete data
                    concept_title = concept_data.get('concept_title', f"Concept {idx+1}")
                    concept_explanation = concept_data.get('concept_explanation', "No explanation provided")
                    
                    # Extract timestamp information
                    timestamp_start = concept_data.get('source_video_timestamp_start_seconds')
                    timestamp_end = concept_data.get('source_video_timestamp_end_seconds')
                    
                    # Store the concept
                    concept_id = store.add_key_concept(
                        file_id=db_file_id,
                        concept_title=concept_title,
                        concept_explanation=concept_explanation,
                        source_page_number=concept_data.get("source_page_number"),
                        source_video_timestamp_start_seconds=timestamp_start,
                        source_video_timestamp_end_seconds=timestamp_end
                        # display_order=concept_data.get("display_order")
                    )
                    
                    # Track the concept ID for later use
                    concept_data["id"] = concept_id
                    key_concept_db_ids.append(concept_id)
                    
                    logger.info(f"Stored concept {idx+1}/{len(key_concepts_list)}: '{concept_title[:30]}...'")
                except Exception as concept_store_error:
                    logger.error(f"Error storing concept {idx+1}/{len(key_concepts_list)}: {concept_store_error}", exc_info=True)
                    # Continue to next concept rather than failing completely
                    continue
            
            if key_concept_db_ids:
                logger.info(f"Successfully stored {len(key_concept_db_ids)} key concepts with IDs: {key_concept_db_ids}")
                
                # --- Flashcard & Quiz Generation ---
                try:
                    # Import required utility functions
                    from flashcard_quiz_utils import (
                        generate_flashcard_from_key_concept,
                        generate_mcq_from_key_concepts,
                        generate_true_false_from_key_concepts
                    )
                    
                    # Process each concept
                    for concept_data in key_concepts_list:
                        # Skip concepts without an ID
                        if not concept_data.get("id"):
                            logger.warning(f"Skipping concept without ID: {concept_data.get('concept_title', 'Unknown')[:30]}")
                            continue
                            
                        # Generate learning materials for this concept with separate error handling for each type
                        concept_title = concept_data.get('concept_title', 'Unknown')[:30]
                        
                        # Generate flashcard with error handling
                        try:
                            flashcard = generate_flashcard_from_key_concept(concept_data)
                            store.add_flashcard(
                                file_id=db_file_id,
                                key_concept_id=concept_data["id"],
                                question=flashcard["question"],
                                answer=flashcard["answer"]
                            )
                            logger.info(f"Generated flashcard for concept: {concept_title}")
                        except Exception as flash_error:
                            logger.error(f"Error generating flashcard: {str(flash_error)[:100]}")
                        
                        # Generate MCQ with error handling
                        try:
                            mcq = generate_mcq_from_key_concepts(concept_data, key_concepts_list)
                            store.add_quiz_question(
                                file_id=db_file_id,
                                key_concept_id=concept_data["id"],
                                question=mcq["question"],
                                question_type="MCQ",
                                correct_answer=mcq["correct_answer"],
                                distractors=mcq["distractors"],
                                is_custom=False
                            )
                            logger.info(f"Generated MCQ for concept: {concept_title}")
                        except Exception as mcq_error:
                            logger.error(f"Error generating MCQ question: {str(mcq_error)[:100]}")
                        
                        # Generate T/F question with error handling
                        try:
                            tf = generate_true_false_from_key_concepts(concept_data, key_concepts_list)
                            store.add_quiz_question(
                                file_id=db_file_id,
                                key_concept_id=concept_data["id"],
                                question=tf["question"],
                                question_type="TF",
                                correct_answer=tf["correct_answer"],
                                distractors=tf["distractors"],
                                is_custom=False
                            )
                            logger.info(f"Generated T/F question for concept: {concept_title}")
                        except Exception as tf_error:
                            logger.error(f"Error generating T/F question: {str(tf_error)[:100]}")
                    
                    logger.info(f"Completed flashcard and quiz generation for file ID: {db_file_id}")
                except Exception as materials_error:
                    logger.error(f"Error in flashcard/quiz generation process: {materials_error}", exc_info=True)
            
            # Handle case where key concepts were attempted but none were generated/valid
            if key_concepts_list is not None and not key_concepts_list:
                logger.info(f"No key concepts generated for file ID: {file_id} despite successful processing")
                # Mark file as processed since we attempted key concept generation
                store.update_file_processing_status(int(file_id), True)
    
    except Exception as kc_error:  # Outer try-except for all key concept processing
        logger.error(f"Error during key concept generation or storage for {filename} (File ID: {file_id}): {kc_error}", exc_info=True)
        # Still mark as processed to prevent infinite retries
        store.update_file_processing_status(int(file_id), True, status="warning",
                                    error_message=f"Error during key concept generation: {str(kc_error)[:100]}")
        
        # Check for empty transcription - only if we haven't already handled it in the try-except above
        if not full_transcription:  # Changed from 'else' to proper conditional check
            logger.info(f"Skipping key concept generation for {filename} (File ID: {file_id}) as full transcription is empty.")
            # Mark as processed since there's nothing to process
            store.update_file_processing_status(int(file_id), True)
        
        # Mark processing as complete regardless of outcome
        logger.info(f"Finished processing YouTube video: {filename}")
        return
    # --- END YOUTUBE HANDLING ---
    file_path = None # Initialize file_path to None
    # Wrap entire process in a try-except block to catch errors
    try: # Main try block starts here
        # Use the global store instance
        # Create a temporary file path and download inside the 'with' block
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as temp_file:
            file_path = temp_file.name
            logger.debug(f"Created temporary file at {file_path}")
            
            # Download the file using user_gc_id and filename
            file_data = download_from_gcs(user_gc_id, filename)
            if not file_data:
                # Need to clean up the temp file manually if download fails *before* writing
                os.remove(file_path)
                file_path = None # Prevent cleanup in finally block
                raise HTTPException(status_code=404, detail="File not found.")
            
            # Write the downloaded data to the open temporary file
            temp_file.write(file_data)
            # No need to close temp_file, 'with' handles it
            
        # Now file_path points to the populated temporary file
        logger.info(f"Successfully downloaded and saved {filename} to {file_path}")

        # Determine file type and process accordingly
        _, ext = os.path.splitext(filename)
        ext = ext.lower().strip('.')

        if ext in ["mp4", "mov", "avi", "mkv", "webm", "mp3", "wav", "m4a"]: # Handle common video/audio types
            # VIDEO/AUDIO PATH
            logger.info(f"Processing video/audio file: {filename}")
            # Transcribe (assuming transcribe_audio_chunked works for video files too)
            language = 'en' # Assuming default language, adjust if needed
            transcribed_data = await transcribe_audio_chunked(file_path, language)
            logger.info(f"Transcription complete. Segments: {len(transcribed_data)}")

            # --- Prepare video data structure with embeddings for storage ---+
            processed_video_data = []
            all_small_chunks = [] # List to hold tuples of (segment_index, chunk_text)
            segment_chunk_map = {} # Map segment index to its list of small chunk texts

            logger.info(f"Chunking content for {len(transcribed_data)} video segments...")
            for i, segment in enumerate(transcribed_data):
                segment_content = segment.get('content', '')
                if segment_content.strip():
                    small_chunks = chunk_text(segment_content)
                    non_empty_small_chunks = [chk for chk in small_chunks if chk.strip()]
                    segment_chunk_map[i] = non_empty_small_chunks
                    for chunk_text_content in non_empty_small_chunks:
                        all_small_chunks.append((i, chunk_text_content))
                else:
                    segment_chunk_map[i] = []

            # Generate embeddings for all small chunks across the video in one batch
            logger.info(f"Generating embeddings for {len(all_small_chunks)} small chunks across video...")
            all_small_chunk_texts = [text for _, text in all_small_chunks]
            all_embeddings = []
            if all_small_chunk_texts:
                all_embeddings = get_text_embeddings_in_batches(all_small_chunk_texts)

            if len(all_embeddings) != len(all_small_chunks):
                logger.error(f"Mismatch between small chunk count ({len(all_small_chunks)}) and embedding count ({len(all_embeddings)}). Some chunks may not be stored with embeddings.")
                # Handle mismatch if needed - maybe retry? For now, proceed but some chunks won't get embeddings.
                embedding_dict = {}
            else:
                # Create a dictionary mapping chunk text to its embedding for easier lookup
                # Using index might be safer if chunk texts aren't unique
                embedding_dict = {all_small_chunks[i][1] : all_embeddings[i] for i in range(len(all_small_chunks))}
                # Consider using a tuple (segment_index, chunk_index) as key if texts aren't unique enough

            # Reconstruct processed_video_data with segment info and its embedded small chunks
            logger.info("Structuring video data for storage...")
            for i, segment in enumerate(transcribed_data):
                segment_content = segment.get('content', '')
                structured_item = {
                    'start_time': segment.get('start_time'),
                    'end_time': segment.get('end_time'),
                    'content': segment_content, # Keep segment content for Segment record
                    'chunks': [] # Initialize chunks list
                }

                # Populate with the small chunks and their embeddings for this segment
                small_chunks_for_segment = segment_chunk_map.get(i, [])
                for small_chunk_text in small_chunks_for_segment:
                    embedding = embedding_dict.get(small_chunk_text) # Look up embedding
                    if embedding is not None:
                        structured_item['chunks'].append({
                            'embedding': embedding,
                            'content': small_chunk_text # Store the small chunk content
                        })
                    else:
                        # Optionally store chunk without embedding if lookup failed (due to mismatch)
                        logger.warning(f"Could not find embedding for chunk in segment {i}. Storing chunk without embedding.")
                        structured_item['chunks'].append({
                            'embedding': None, # Explicitly None
                            'content': small_chunk_text
                        })

                processed_video_data.append(structured_item)
            logger.info("Finished structuring video data with embeddings.")

            # --- Generate and Save Key Concepts for Video ---
            full_transcript_text = " ".join([seg.get('content', '') for seg in transcribed_data if seg.get('content', '').strip()])
            if full_transcript_text:
                logger.info(f"Attempting to generate key concepts for video: {filename}")
                try:
                    key_concepts = generate_key_concepts_dspy(
                        document_text=full_transcript_text
                    )
                    if key_concepts:
                        for i_kc, concept in enumerate(key_concepts):
                            store.add_key_concept(
                                file_id=int(file_id),
                                concept_title=concept.get("concept_title"),
                                concept_explanation=concept.get("concept_explanation"),
                                display_order=i_kc + 1,
                                source_page_number=concept.get("source_page_number"), 
                                source_video_timestamp_start_seconds=concept.get("source_video_timestamp_start_seconds"),
                                source_video_timestamp_end_seconds=concept.get("source_video_timestamp_end_seconds")
                            )
                        logger.info(f"Saved {len(key_concepts)} key concepts for video: {filename}")
                    else:
                        logger.warning(f"No key concepts generated or returned empty for video: {filename}.")
                except Exception as kc_err:
                    logger.error(f"Error during key concept generation/saving for video {filename}: {kc_err}", exc_info=True)
            else:
                logger.warning(f"No transcript content found to generate key concepts for video file: {filename}")
            # --------------------------------------------------

            # Save the segmented video data with embeddings
            if processed_video_data:
                store.update_file_with_chunks(user_id=int(user_id), filename=filename, file_type="video", extracted_data=processed_video_data)
                logger.info("Processed and stored video segments.")
            else:
                logger.warning("No processed video data with chunks generated, skipping storage update.")

        elif ext == "pdf":
            logger.info("Processing PDF document...")
            from processors.pdf_processor import PDFProcessor
            
            # Initialize the PDF Processor
            pdf_processor = PDFProcessor(store)
            
            # Process the PDF using the dedicated processor
            result = await pdf_processor.process(
                file_data=file_data,
                file_id=int(file_id),
                user_id=int(user_id),
                filename=filename
            )
            
            if not result.get('success', False):
                logger.error(f"PDF processing failed: {result.get('error', 'unknown error')}")
                return False
                
            logger.info(f"PDF processing complete. Pages: {result.get('page_count', 0)}, Chunks: {result.get('chunk_count', 0)}")
            
            # PDF processor already handled chunk addition and key concept generation
            # No need for any further processing for PDFs

        elif ext in ["txt", "jpg", "jpeg", "png", "gif"]:
            logger.info(f"Processing {ext} file...")
            from processors.text_processor import TextProcessor
            
            # Initialize the Text Processor
            text_processor = TextProcessor(store)
            
            # Process the file using the dedicated processor
            result = await text_processor.process(
                file_data=file_data,
                file_id=int(file_id),
                user_id=int(user_id),
                filename=filename
            )
            
            if not result.get('success', False):
                logger.error(f"Text/image processing failed: {result.get('error', 'unknown error')}")
                return False
                
            logger.info(f"Processing complete. File type: {result.get('file_type', 'unknown')}, Chunks: {result.get('chunk_count', 0)}")
            
            # Text processor already handled chunk addition and key concept generation
            # No need for any further processing
                
        else:
            logger.warning(f"Unsupported file type: {ext}")

    except Exception as e:
        logger.error(f"Error processing file {filename} (ID: {file_id}): {e}", exc_info=True)

    finally:
        # Check if file_path exists and is not None before removing
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temporary file: {file_path}")
        # Check if store exists (though it should if used above)
        # Sessions are managed internally by DocSynthStore methods, so no explicit close here.
        # Clear Whisper model from memory if loaded
        # global whisper_model
        # if whisper_model:
        #     # Explicitly delete and collect garbage to free GPU memory if applicable
        #     del whisper_model
        #     whisper_model = None
        #     gc.collect()
        #     if torch.cuda.is_available():
        #         torch.cuda.empty_cache()
        #     logger.info("Cleaned up Whisper model from memory.")
        # else:
        #     logger.info("Whisper model was not loaded or already cleaned up.")
        pass # Ensure finally block is not empty if all cleanup is commented out

async def process_query_data(id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """Processes a user query and generates a response using SyntextAgent with enhanced RAG."""
    try:
        # Get conversation history in formatted form
        formatted_history = store.format_user_chat_history(history_id, id)
        
        # Try enhanced RAG pipeline first
        try:
            logger.info(f"Processing query with enhanced RAG: '{message}'")
            # Import enhanced RAG utilities
            from rag_utils import process_query, hybrid_search, cross_encoder_rerank, smart_chunk_selection
            
            # Process and expand the query
            rewritten_query, expanded_terms = process_query(message, formatted_history)
            logger.info(f"Original query: '{message}', Rewritten: '{rewritten_query}'")
            if expanded_terms:
                logger.info(f"Query expansion terms: {', '.join(expanded_terms)}")
            
            # Generate embeddings for the query
            query_embedding = get_text_embedding(rewritten_query)
            
            # Enhanced retrieval: get more candidates for reranking
            vector_results = store.query_chunks_by_embedding(id, query_embedding, top_k=15)
            
            # If we have expanded terms, try to get additional results and combine them
            additional_results = []
            if expanded_terms:
                for term in expanded_terms[:3]:  # Limit to top 3 expansion terms
                    try:
                        term_embedding = get_text_embedding(term)
                        term_results = store.query_chunks_by_embedding(id, term_embedding, top_k=5)
                        additional_results.extend(term_results)
                    except Exception as term_error:
                        logger.warning(f"Error retrieving results for expansion term '{term}': {term_error}")
            
            # Combine all retrieved results
            all_results = vector_results + additional_results
            
            # Log file types being retrieved to verify all file types work with RAG
            file_types = {}
            for result in all_results:
                if 'file_name' in result:
                    file_ext = result['file_name'].split('.')[-1].lower() if '.' in result['file_name'] else 'unknown'
                    file_types[file_ext] = file_types.get(file_ext, 0) + 1
            if file_types:
                logger.info(f"Retrieved content from file types: {file_types}")
                
            # Deduplicate results by segment ID
            seen_ids = set()
            unique_results = []
            for result in all_results:
                segment_id = result.get('meta_data', {}).get('segment_id', None)
                if segment_id and segment_id not in seen_ids:
                    seen_ids.add(segment_id)
                    unique_results.append(result)
            
            # Apply cross-encoder reranking
            try:
                reranked_results = cross_encoder_rerank(rewritten_query, unique_results, top_k=15)
                logger.info(f"Reranked {len(unique_results)} results to {len(reranked_results)} top results")
            except Exception as rerank_error:
                logger.warning(f"Reranking error: {rerank_error}, falling back to original ranking")
                reranked_results = unique_results[:15] if len(unique_results) > 15 else unique_results
            
            # Select chunks that fit token budget while maximizing relevance and diversity
            context_chunks = smart_chunk_selection(reranked_results, rewritten_query)
            
            # Generate response using the enhanced context
            response = syntext.query_pipeline(message, formatted_history, context_chunks, language, comprehension_level)
            logger.info("Enhanced RAG pipeline completed successfully")
            
        except Exception as rag_error:
            # Fallback to original RAG pipeline if enhanced version fails
            logger.warning(f"Enhanced RAG pipeline failed: {rag_error}. Falling back to original pipeline.")
            
            # Original RAG pipeline as a fallback
            query_embedding = get_text_embedding(message)
            topK_chunks = store.query_chunks_by_embedding(id, query_embedding, top_k=10)
            response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
            logger.info("Fallback to original RAG pipeline successful")
        
        # Save response and notify user (common for both pipelines)
        store.add_message(content=response, sender='bot', user_id=id, chat_history_id=history_id)
        await websocket_manager.send_message(id, "message_received", {"status": "success", "history_id": history_id, "message": response})
    
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
