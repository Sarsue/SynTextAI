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
    # Debug log to confirm function is being called
    logger.info(f"========== STARTING BACKGROUND TASK: process_file_data for {filename} (ID: {file_id}) ===========")
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate key concepts, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id}, GCS_ID: {user_gc_id}, Lang: {language}, Level: {comprehension_level})")
    
    try:
        # Update status to processing
        store.update_file_status(int(file_id), "processing", "File is being processed")
        
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
            logger.info(f"Using {processor.__class__.__name__} for file: {filename}")
        else:
            # No processor available for this file type
            logger.error(f"No processor available for file type: {filename}")
            store.update_file_status(int(file_id), "error", "Unsupported file type. Please upload a supported format.")
            store.update_file_processing_status(int(file_id), True)  # Mark as processed to prevent retries
            return
        
        # Process the file using the selected processor
        try:
            # Download the file from GCS
            logger.info(f"Downloading file {filename} from GCS")
            file_data = download_from_gcs(user_gc_id, filename)
            
            if file_data is None:
                logger.error(f"Failed to download file {filename} from GCS")
                store.update_file_status(int(file_id), "error", "Failed to download file from storage")
                return
                
            logger.info(f"Successfully downloaded file {filename}, size: {len(file_data)} bytes")
            
            # Process the file using the selected processor
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
            
            if not result.get("success", False):
                logger.error(f"Processing failed: {result.get('error', 'Unknown error')}")
                store.update_file_status(int(file_id), "error", result.get('error', 'Processing failed')[:200])
                return  # Exit without marking as processed so it can be retried
                
            logger.info(f"Processing completed successfully for file_id: {file_id}")
            # Update file status to processed
            store.update_file_processing_status(int(file_id), True)
            return
            
        except Exception as e:
            logger.error(f"Error in file processing: {e}", exc_info=True)
            store.update_file_status(int(file_id), "error", str(e)[:200])
            return
            
    except Exception as e:
        logger.error(f"Fatal error in file processing pipeline: {e}", exc_info=True)
        try:
            store.update_file_status(int(file_id), "error", f"System error: {str(e)[:150]}")
        except Exception:
            pass  # If we can't update the status, we've already logged the error
    
    # We've either had a fatal error or processing has completed
    return

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
