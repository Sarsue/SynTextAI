import logging
import os
import tempfile
from contextlib import contextmanager
import asyncio
from faster_whisper import WhisperModel
import yt_dlp
from api.utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from api.repositories.repository_manager import RepositoryManager
from api.llm_service import get_text_embeddings_in_batches, get_text_embedding, token_count, MAX_TOKENS_CONTEXT, generate_key_concepts_dspy
from api.syntext_agent import SyntextAgent
import stripe
from api.websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import BackgroundTasks, HTTPException
import gc
from typing import Optional
from api.models.async_db import get_database_url

# Get centralized async database URL

# Load environment variables
load_dotenv()

# Load Whisper model once at startup
whisper_model = None # Initialize as None, will be loaded by load_whisper_model

def load_whisper_model_if_needed():
    global whisper_model
    if whisper_model is None:
        logger.info("Loading faster-whisper model...")
        # Consider making model size/params configurable if needed later
        whisper_model = WhisperModel("small", device="cpu", compute_type="int8", download_root="/app/models")
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

DATABASE_URL = get_database_url()

store = RepositoryManager(database_url=DATABASE_URL)
syntext = SyntextAgent()

# Model is only loaded when needed to save memory
@contextmanager
def load_whisper_model():
    model = WhisperModel("small", device="cpu", compute_type="int8")
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
    # Debug log to confirm function is being called
    logger.info(f"========== STARTING BACKGROUND TASK: process_file_data for {filename} (ID: {file_id}) ===========")
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate key concepts, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level})")
    
    # Helper function to update file status with proper error handling
    async def update_status(status: str, error_msg: str = None):
        try:
            from api.worker import update_file_status
            await update_file_status(int(file_id), status)
            logger.info(f"Updated file {file_id} status to {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update file {file_id} status to {status}: {str(e)}", exc_info=True)
            return False
    
    try:
        # Get the file object and validate status - don't process if already processing or processed
        file = store.file_repo.get_file_by_id(int(file_id))
        if not file:
            logger.error(f"File not found with ID: {file_id}")
            return
            
        # Handle both dictionary and object access patterns
        def get_file_attr(file_obj, attr, default=None):
            if hasattr(file_obj, attr):
                return getattr(file_obj, attr)
            elif isinstance(file_obj, dict) and attr in file_obj:
                return file_obj[attr]
            return default
            
        def set_file_attr(file_obj, attr, value):
            if hasattr(file_obj, attr):
                setattr(file_obj, attr, value)
            elif isinstance(file_obj, dict):
                file_obj[attr] = value
                
        # Update file type if not set
        file_type = get_file_attr(file, 'file_type')
        if not file_type:
            if "youtube.com" in filename or "youtu.be" in filename:
                new_file_type = "youtube"
            elif filename.lower().endswith(".pdf"):
                new_file_type = "pdf"
            else:
                new_file_type = "unknown"
                
            set_file_attr(file, 'file_type', new_file_type)
            if hasattr(file, 'session'):
                file.session.commit()
            elif hasattr(store.file_repo, 'session'):
                store.file_repo.session.commit()
        
        # Check if file is already being processed elsewhere
        processing_status = get_file_attr(file, 'processing_status')
        if processing_status in ["processing", "extracting"]:
            logger.warning(f"File {file_id} is already being processed (status: {processing_status}). Skipping.")
            return
        elif processing_status == "processed":
            logger.info(f"File {file_id} is already processed. Skipping.")
            return
            
        # Update status to EXTRACTING
        success = await update_status("extracting")
        if not success:
            logger.error(f"Failed to update status to extracting for file {file_id}")
            return
        
        logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level})")
        
        # Fix import issues by doing a direct import with the correct sys.path
        import sys
        import os
        
        # Get the absolute path to the app directory (parent of api)
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Add app directory to the path if not already there
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
            
        # Now we can safely import the factory using an absolute import path
        from api.processors.factory import FileProcessingFactory
        
        # Initialize the processor factory with the store
        factory = FileProcessingFactory(store)
        
        # Get the appropriate processor for this file
        processor = factory.get_processor(filename)
        
        # Log which processor was selected
        if processor:
            logger.info(f"[TRACE] [process_file_data] Using processor: {processor.__class__.__name__}")
        else:
            # No processor available for this file type
            logger.error(f"[ERROR] [process_file_data] No processor available for file type: {filename}")
            # Note: Status is inferred rather than explicitly stored
            return
        
        # Process the file using the selected processor
        try:
            # Check if this is a YouTube URL - if so, we don't need to download from GCS
            is_youtube_url = "youtube.com" in filename or "youtu.be" in filename
            
            if is_youtube_url:
                logger.info(f"[TRACE] [process_file_data] Processing YouTube video directly: {filename}")
                # For YouTube, we don't need the file data - we'll extract the transcript directly
                result = await processor.process(
                    user_id=user_id,
                    file_id=file_id,
                    filename=filename,
                    file_url=file_url,
                    user_gc_id=user_gc_id,
                    language=language,
                    comprehension_level=comprehension_level
                )
                
                # Update status to EXTRACTED after successful extraction
                if result.get("success", False):
                    success = await update_status("extracted")
                    if not success:
                        logger.error(f"Failed to update status to extracted for file {file_id}")
                        return
            else:
                # For non-YouTube files, download from GCS as before
                logger.info(f"[TRACE] [process_file_data] Downloading file {filename} from GCS")
                file_data = download_from_gcs(user_gc_id, filename)
                
                if file_data is None:
                    logger.error(f"[ERROR] [process_file_data] Failed to download file {filename} from GCS")
                    # Note: Status is inferred rather than explicitly stored
                    return
                    
                logger.info(f"[TRACE] [process_file_data] Successfully downloaded file {filename}, size: {len(file_data)} bytes")
                
                logger.info(f"[TRACE] [process_file_data] Processing file with {processor.__class__.__name__}")
                try:
                    result = await processor.process(
                        user_id=user_id,
                        file_id=file_id,
                        filename=filename,
                        file_data=file_data,  # Pass the file data to the processor
                        file_url=file_url,
                        user_gc_id=user_gc_id,
                        language=language,
                        comprehension_level=comprehension_level
                    )
                    logger.info(f"[TRACE] [process_file_data] Processor completed with result: {result.get('success', False)}")
                except Exception as proc_err:
                    logger.error(f"[ERROR] [process_file_data] Error in processor: {str(proc_err)}", exc_info=True)
                    result = {"success": False, "error": str(proc_err)}
                
                # Update status to EXTRACTED after successful extraction
                if result.get("success", False):
                    logger.info("[TRACE] [process_file_data] Updating status to EXTRACTED")
                    file = await store.file_repo.get_file_by_id(int(file_id))  # Reload to avoid stale data
                    if file is not None:
                        try:
                            if hasattr(file, 'processing_status'):
                                file.processing_status = "extracted"
                            elif isinstance(file, dict):
                                file['processing_status'] = "extracted"
                            
                            if hasattr(store.file_repo, 'session'):
                                store.file_repo.session.commit()
                                logger.info(f"[TRACE] [process_file_data] Updated file {file_id} status to extracted")
                            else:
                                logger.error("[ERROR] [process_file_data] No session available to commit")
                        except Exception as update_err:
                            logger.error(f"[ERROR] [process_file_data] Failed to update status to extracted: {str(update_err)}", exc_info=True)
                    else:
                        logger.error(f"[ERROR] [process_file_data] File with ID {file_id} not found when trying to update status to extracted")
                
                if not result.get("success", False):
                    error_msg = result.get('error', 'Unknown error')
                    logger.error(f"[ERROR] [process_file_data] Processing failed for file ID {file_id}: {error_msg}")
                    # Update status to FAILED
                    success = await update_status("failed", error_msg=error_msg)
                    if not success:
                        logger.error(f"Failed to update status to failed for file {file_id}")
                    return  # Exit so processing can be retried
                
                # Update status to PROCESSING   
                logger.info("[TRACE] [process_file_data] Updating status to PROCESSING")
                success = await update_status("processing")
                if not success:
                    logger.error(f"Failed to update status to processing for file {file_id}")
                    return
                
                # Generate key concepts and other learning materials
                # This happens implicitly in the processor's process method
                
                # Update status to PROCESSED after successful processing
                logger.info("[TRACE] [process_file_data] Updating status to PROCESSED")
                success = await update_status("processed")
                if not success:
                    logger.error(f"Failed to update status to processed for file {file_id}")
                    return
                
                logger.info(f"[TRACE] [process_file_data] Updated file {file_id} status to processed")
            return
            
        except Exception as e:
            logger.error(f"Error in file processing: {e}", exc_info=True)
            # Update status to FAILED
            success = await update_status("failed", error_msg=str(e))
            if not success:
                logger.error(f"Failed to update status to failed for file {file_id}")
            return
            
    except Exception as e:
        logger.error(f"Fatal error in file processing pipeline: {e}", exc_info=True)
        # Update status to FAILED
        success = await update_status("failed", error_msg=str(e))
        if not success:
            logger.error(f"Failed to update status to failed for file {file_id} in outer exception handler")
    
    # We've either had a fatal error or processing has completed
    return

async def process_query_data(id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """Processes a user query and generates a response using SyntextAgent with enhanced RAG."""
    try:
        # Get conversation history in formatted form
        formatted_history = await store.user_repo.format_user_chat_history(history_id, id)
        
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
            vector_results = await store.file_repo.query_chunks_by_embedding(id, query_embedding, top_k=15)
            
            # If we have expanded terms, try to get additional results and combine them
            additional_results = []
            if expanded_terms:
                for term in expanded_terms[:3]:  # Limit to top 3 expansion terms
                    try:
                        term_embedding = get_text_embedding(term)
                        term_results = await store.file_repo.query_chunks_by_embedding(id, term_embedding, top_k=5)
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
            topK_chunks = await store.file_repo.query_chunks_by_embedding(id, query_embedding, top_k=10)
            response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
            logger.info("Fallback to original RAG pipeline successful")
        
        # Save response and notify user (common for both pipelines)
        await store.user_repo.add_message(content=response, sender='bot', user_id=id, chat_history_id=history_id)

        # Send WebSocket notification with error handling
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "success", "history_id": history_id, "message": response})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket notification for successful query processing: {str(ws_error)}")
            # Don't fail the entire operation for WebSocket issues
    
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        # Send WebSocket error notification with error handling
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "error", "error": str(e)})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket error notification: {str(ws_error)}")
            # Don't fail the entire operation for WebSocket issues
        raise HTTPException(status_code=500, detail="Query processing failed")

async def delete_user_task(user_id: str, user_gc_id: str):
    """Deletes a user's account, subscription, and associated files."""
    try:
        user_sub = await store.user_repo.get_subscription(user_id)
        if user_sub and user_sub.get("status") == "active":
            stripe_sub_id = user_sub.get("stripe_subscription_id")
            stripe_customer_id = user_sub.get("stripe_customer_id")

            # Cancel the subscription
            if stripe_sub_id:
                stripe.Subscription.delete(stripe_sub_id)
                logger.info(f"Subscription {stripe_sub_id} canceled.")

            # Remove payment methods and delete the customer
            if stripe_customer_id:
                payment_methods = await stripe.PaymentMethod.list_async(customer=stripe_customer_id, type="card")
                for method in payment_methods.auto_paging_iter():
                    await stripe.PaymentMethod.detach_async(method.id)
                    logger.info(f"Payment method {method.id} detached.")
                
                await stripe.Customer.delete_async(stripe_customer_id)
                logger.info(f"Stripe customer {stripe_customer_id} deleted.")

        # Delete files and user account
        files = await store.file_repo.get_files_for_user(user_id)

        # Schedule all file deletions concurrently
        await asyncio.gather(
            *(asyncio.to_thread(delete_from_gcs, user_gc_id, f["name"]) for f in files),
            return_exceptions=True
        )

        success = await store.user_repo.delete_user_account(user_id)
        if success:
            logger.info(f"User account {user_id} deleted successfully")
        else:
            logger.error(f"Failed to delete user account {user_id}")

      

    except Exception as e:
        logger.error(f"Error during user deletion: {e}")
        raise HTTPException(status_code=500, detail="User deletion failed")


async def generate_flashcards_from_key_concepts(key_concepts: list) -> list:
    """
    Generate flashcards from key concepts using Gemini.
    
    Args:
        key_concepts: List of key concept dictionaries
        
    Returns:
        List of flashcard dictionaries with 'front' and 'back' keys
    """
    if not key_concepts:
        logger.warning("No key concepts provided for flashcard generation")
        return []
    
    try:
        flashcards = []
        for concept in key_concepts:
            # Extract concept information (handle different formats from PDFs vs YouTube)
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            # Create a simple flashcard
            flashcards.append({
                'front': concept_title.strip(),
                'back': concept_explanation.strip()
            })
            
            # Create 1-2 additional flashcards asking about specific aspects of the concept
            # This could be enhanced with a real LLM call for more sophisticated flashcards
            if len(concept_explanation.split()) > 20:
                parts = concept_explanation.split(". ")
                if len(parts) > 1:
                    key_detail = parts[0]
                    flashcards.append({
                        'front': f"What is a key detail about {concept_title}?",
                        'back': key_detail
                    })
        
        logger.info(f"Generated {len(flashcards)} flashcards from {len(key_concepts)} key concepts")
        return flashcards
        
    except Exception as e:
        logger.error(f"Error generating flashcards: {e}", exc_info=True)
        return []


async def generate_mcq_from_key_concepts(key_concepts: list) -> list:
    """
    Generate multiple choice questions from key concepts.
    
    Args:
        key_concepts: List of key concept dictionaries
        
    Returns:
        List of MCQ dictionaries with question, options, and answer
    """
    if not key_concepts:
        logger.warning("No key concepts provided for MCQ generation")
        return []
    
    try:
        mcqs = []
        for concept in key_concepts:
            # Extract concept information (handle different formats)
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            # Create question from concept title
            question = f"What is {concept_title}?"
            
            # Create a correct answer from concept explanation
            correct_answer = concept_explanation.split(".")[0].strip()
            if len(correct_answer) < 10 and len(concept_explanation.split(".")) > 1:
                correct_answer = concept_explanation.split(".")[0] + "." + concept_explanation.split(".")[1]
                correct_answer = correct_answer.strip()
            
            # Generate 3 simple distractors (this would be improved with a real LLM call)
            distractors = [
                f"An unrelated concept not covered in this material.",
                f"The opposite of what the material actually explains about this topic.",
                f"A common misconception about {concept_title.lower()}."
            ]
            
            # Combine correct answer and distractors into options
            options = [correct_answer] + distractors
            
            mcqs.append({
                'question': question,
                'options': options,
                'answer': correct_answer
            })
        
        logger.info(f"Generated {len(mcqs)} MCQs from {len(key_concepts)} key concepts")
        return mcqs
        
    except Exception as e:
        logger.error(f"Error generating MCQs: {e}", exc_info=True)
        return []


async def generate_true_false_from_key_concepts(key_concepts: list) -> list:
    """
    Generate true/false questions from key concepts.
    
    Args:
        key_concepts: List of key concept dictionaries
        
    Returns:
        List of true/false dictionaries with statement and is_true flag
    """
    if not key_concepts:
        logger.warning("No key concepts provided for true/false generation")
        return []
    
    try:
        tf_questions = []
        for concept in key_concepts:
            # Extract concept information (handle different formats)
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            # Create a true statement using the actual concept
            tf_questions.append({
                'statement': f"{concept_title} refers to {concept_explanation.split('.')[0]}.",
                'is_true': True
            })
            
            # Create a false statement by modifying the concept slightly
            false_statement = f"{concept_title} is completely unrelated to the topic covered in this material."
            tf_questions.append({
                'statement': false_statement,
                'is_true': False
            })
            
        logger.info(f"Generated {len(tf_questions)} True/False questions from {len(key_concepts)} key concepts")
        return tf_questions
        
    except Exception as e:
        logger.error(f"Error generating True/False questions: {e}", exc_info=True)
        return []


# New adapter functions to bridge the gap between the processor and the existing functions

async def generate_flashcards_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate flashcards for a single concept.
    
    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept
        
    Returns:
        List of flashcard items
    """
    logger.info(f"Generating flashcards for concept: '{concept_title[:30]}...'")
    
    # Create a single concept in the format expected by the original function
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    
    # Directly await the async function since we're in an async context
    flashcards = await generate_flashcards_from_key_concepts(concept)
    logger.info(f"Generated {len(flashcards)} flashcards for concept '{concept_title[:30]}...'")
    return flashcards

async def generate_mcqs_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate multiple choice questions for a single concept.
    
    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept
        
    Returns:
        List of MCQ items
    """
    logger.info(f"Generating MCQs for concept: '{concept_title[:30]}...'")
    
    # Create a single concept in the format expected by the original function
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    
    # Directly await the async function since we're in an async context
    mcqs = await generate_mcq_from_key_concepts(concept)
    logger.info(f"Generated {len(mcqs)} MCQs for concept '{concept_title[:30]}...'")
    return mcqs

async def generate_true_false_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate true/false questions for a single concept.
    
    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept
        
    Returns:
        List of true/false questions
    """
    logger.info(f"Generating True/False questions for concept: '{concept_title[:30]}...'")
    
    # Create a single concept in the format expected by the original function
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    
    # Directly await the async function since we're in an async context
    tf_questions = await generate_true_false_from_key_concepts(concept)
    logger.info(f"Generated {len(tf_questions)} True/False questions for concept '{concept_title[:30]}...'")
    return tf_questions
