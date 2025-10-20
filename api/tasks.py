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
from fastapi import HTTPException
import gc
from typing import Optional
from api.models.async_db import get_database_url
from api.processors.factory import FileProcessingFactory

# Load environment variables
load_dotenv()

# Load Whisper model once at startup
whisper_model = None  # Initialize as None, will be loaded by load_whisper_model

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

class FileUtils:
    """Utility class for file-related operations."""

    @staticmethod
    def determine_file_type(filename: str) -> str:
        """Determine file type based on filename."""
        if "youtube.com" in filename or "youtu.be" in filename:
            return "youtube"
        elif filename.lower().endswith(".pdf"):
            return "pdf"
        else:
            return "unknown"

async def transcribe_audio_chunked(file_path: str, language: str = "en", chunk_duration_ms: int = 30000, overlap_ms: int = 5000) -> tuple[list, any]:
    model = load_whisper_model_if_needed()  # Ensure model is loaded
    transcribe_params = {
        'language': language if language else None,  # None for auto-detect, or specify lang code
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
        'format': 'bestaudio/best',  # Prioritize best audio quality
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,  # Sometimes helps with SSL issues
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',  # Or 'wav', 'm4a'
            'preferredquality': '192',  # Standard quality
        }],
    }

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

async def handle_processing_error(file_id: int, error_msg: str) -> dict:
    """Handle errors during file processing by updating status and logging."""
    error_msg = error_msg[:1000]  # Truncate for consistency
    logger.error(error_msg)
    await store.file_repo.update_file_status(file_id, "failed")
    return {"success": False, "error_message": error_msg}

async def process_file_data(
    user_gc_id: str,
    user_id: str,
    file_id: str,
    filename: str,
    file_url: str,
    is_youtube: bool = False,
    language: str = "English",
    comprehension_level: str = "Beginner"
):
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate key concepts, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id})")

    try:
        # Validate inputs
        try:
            file_id_int = int(file_id)
        except ValueError:
            return await handle_processing_error(file_id, f"Invalid file_id: {file_id}")

        if not filename or len(filename) > 255:
            return await handle_processing_error(file_id, f"Invalid filename: {filename}")

        if language.lower() not in LANGUAGE_CODE_MAP:
            logger.warning(f"Unsupported language: {language}, defaulting to English")
            language = "English"

        # Fetch file once and use throughout
        file = await store.file_repo.get_file_by_id(file_id_int)
        if not file:
            return await handle_processing_error(file_id_int, f"File not found with ID: {file_id}")

        # Update file type if not set
        if not file.get('file_type'):
            new_file_type = FileUtils.determine_file_type(filename)
            success = await store.file_repo.update_file_type(file_id_int, new_file_type)
            if not success:
                return await handle_processing_error(file_id_int, f"Failed to update file type for file ID: {file_id}")

        # Check processing status from the fetched file data (not from DB again)
        if file.get('processing_status') == "processed":
            logger.info(f"File {file_id} is already processed")
            return {"success": True, "final_status": "processed", "error_message": None}

        # Now update status to extracting (worker already set to processing)
        await store.file_repo.update_file_status(file_id_int, "extracting")

        # Initialize processor
        factory = FileProcessingFactory(store)
        processor = factory.get_processor(filename)
        if not processor:
            return await handle_processing_error(file_id_int, f"No processor available for file {filename}")

        logger.info(f"Using processor: {processor.__class__.__name__}")

        # Process file
        is_youtube_url = is_youtube or "youtube.com" in filename or "youtu.be" in filename
        if is_youtube_url:
            logger.info(f"Processing YouTube video: {filename}")
            result = await processor.process(
                user_id=user_id,
                file_id=file_id,
                filename=filename,
                file_url=file_url,
                user_gc_id=user_gc_id,
                language=language,
                comprehension_level=comprehension_level
            )
        else:
            logger.info(f"Downloading file {filename} from GCS")
            file_data = download_from_gcs(user_gc_id, filename)
            if file_data is None:
                return await handle_processing_error(file_id_int, f"Failed to download file {filename} from GCS")

            logger.info(f"Downloaded file {filename}, size: {len(file_data)} bytes")
            result = await processor.process(
                user_id=user_id,
                file_id=file_id,
                filename=filename,
                file_data=file_data,
                file_url=file_url,
                user_gc_id=user_gc_id,
                language=language,
                comprehension_level=comprehension_level
            )

        if not result.get("success", False):
            error_msg = result.get('error', 'Unknown error during file processing')
            return await handle_processing_error(file_id_int, f"Processing failed for file ID {file_id}: {error_msg}")

        await store.file_repo.update_file_status(file_id_int, "processed")
        logger.info(f"File processing completed successfully for {filename}")
        return {
            "success": True,
            "final_status": "processed",
            "message": f"Successfully processed file {filename}",
            "error_message": None
        }

    except Exception as e:
        error_msg = f"Fatal error in file processing pipeline: {str(e)[:1000]}"
        return await handle_processing_error(file_id_int, error_msg)

async def process_query_data(id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """Processes a user query and generates a response using SyntextAgent with enhanced RAG."""
    try:
        # Get conversation history in formatted form
        formatted_history = await store.user_repo.format_user_chat_history(history_id, id)
        
        # Try enhanced RAG pipeline first
        try:
            logger.info(f"Processing query with enhanced RAG: '{message}'")
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
            query_embedding = get_text_embedding(message)
            topK_chunks = await store.file_repo.query_chunks_by_embedding(id, query_embedding, top_k=10)
            response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
            logger.info("Fallback to original RAG pipeline successful")
        
        # Save response and notify user
        await store.user_repo.add_message(content=response, sender='bot', user_id=id, chat_history_id=history_id)
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "success", "history_id": history_id, "message": response})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket notification for successful query processing: {str(ws_error)}")
    
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "error", "error": str(e)})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket error notification: {str(ws_error)}")
        raise HTTPException(status_code=500, detail="Query processing failed")

async def delete_user_task(user_id: str, user_gc_id: str):
    """Deletes a user's account, subscription, and associated files."""
    try:
        user_sub = await store.user_repo.get_subscription(user_id)
        if user_sub and user_sub.get("status") == "active":
            stripe_sub_id = user_sub.get("stripe_subscription_id")
            stripe_customer_id = user_sub.get("stripe_customer_id")
            if stripe_sub_id:
                stripe.Subscription.delete(stripe_sub_id)
                logger.info(f"Subscription {stripe_sub_id} canceled.")
            if stripe_customer_id:
                payment_methods = await stripe.PaymentMethod.list_async(customer=stripe_customer_id, type="card")
                for method in payment_methods.auto_paging_iter():
                    await stripe.PaymentMethod.detach_async(method.id)
                    logger.info(f"Payment method {method.id} detached.")
                await stripe.Customer.delete_async(stripe_customer_id)
                logger.info(f"Stripe customer {stripe_customer_id} deleted.")

        # Delete files and user account
        files = await store.file_repo.get_files_for_user(user_id)
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
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            flashcards.append({
                'front': concept_title.strip(),
                'back': concept_explanation.strip()
            })
            
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
    Generate multiple choice questions from key concepts with improved distractors.
    
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
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            question = f"What is {concept_title}?"
            correct_answer = concept_explanation.split(".")[0].strip()
            if len(correct_answer) < 10 and len(concept_explanation.split(".")) > 1:
                correct_answer = concept_explanation.split(".")[0] + "." + concept_explanation.split(".")[1]
                correct_answer = correct_answer.strip()
            
            # Generate better distractors using embeddings
            distractors = await _generate_smart_distractors(concept, key_concepts, correct_answer)
            
            # Ensure we have enough distractors (fall back to generic if needed)
            if len(distractors) < 3:
                generic_distractors = [
                    "An unrelated concept not covered in this material.",
                    "The opposite of what the material actually explains about this topic.",
                    "A common misconception about this topic."
                ]
                distractors.extend(generic_distractors[:3 - len(distractors)])
            
            options = [correct_answer] + distractors[:3]  # Limit to 3 distractors
            mcqs.append({
                'question': question,
                'options': options,
                'answer': correct_answer
            })
        
        logger.info(f"Generated {len(mcqs)} MCQs from {len(key_concepts)} key concepts with enhanced distractors")
        return mcqs
    except Exception as e:
        logger.error(f"Error generating MCQs: {e}", exc_info=True)
        return []

async def _generate_smart_distractors(concept, all_concepts, correct_answer):
    """Generate smart distractors using embeddings and LLM."""
    distractors = []
    try:
        # Get embeddings for the correct answer and other concepts
        correct_embedding = get_text_embedding([correct_answer])[0]
        other_concepts = [c for c in all_concepts if c != concept]
        
        if other_concepts:
            other_explanations = [c.get('concept_explanation', '') for c in other_concepts]
            other_embeddings = get_text_embeddings_in_batches(other_explanations)
            
            # Find most similar explanations as potential distractors
            similarities = []
            for i, emb in enumerate(other_embeddings):
                if emb and correct_embedding:
                    sim = np.dot(correct_embedding, emb) / (np.linalg.norm(correct_embedding) * np.linalg.norm(emb))
                    similarities.append((i, sim, other_explanations[i]))
            
            # Sort by similarity and select top 3 as distractors
            similarities.sort(key=lambda x: x[1], reverse=True)
            distractors = [exp for _, _, exp in similarities[:3] if exp != correct_answer]
        
        # If not enough, use LLM to generate distractors based on document context
        if len(distractors) < 3:
            additional_distractors = await _generate_llm_distractors(concept, correct_answer)
            distractors.extend(additional_distractors)
        
    except Exception as e:
        logger.warning(f"Error generating smart distractors: {e}")
    
    return distractors[:3]

async def _generate_llm_distractors(concept, correct_answer):
    """Use LLM to generate realistic distractors."""
    try:
        from api.llm_service import generate_explanation_dspy
        prompt = f"Generate 2-3 plausible but incorrect explanations for '{concept.get('concept_title')}' that could be mistaken for the correct one: '{correct_answer}'. Make them sound realistic based on common knowledge."
        distractors = await asyncio.to_thread(generate_explanation_dspy, prompt, "English", "Beginner", 1000)
        return [d.strip() for d in distractors.split('.') if d.strip()][:3]
    except Exception as e:
        logger.warning(f"LLM distractor generation failed: {e}")
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
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
            
            if not concept_title or not concept_explanation:
                continue
                
            tf_questions.append({
                'statement': f"{concept_title} refers to {concept_explanation.split('.')[0]}.",
                'is_true': True
            })
            
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
    
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
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
    
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
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
    
    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    tf_questions = await generate_true_false_from_key_concepts(concept)
    logger.info(f"Generated {len(tf_questions)} True/False questions for concept '{concept_title[:30]}...'")
    return tf_questions