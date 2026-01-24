import logging
import json
import re
import os
import tempfile
from contextlib import contextmanager
import asyncio
from faster_whisper import WhisperModel
import yt_dlp
import requests
from api.utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from api.repositories.repository_manager import RepositoryManager
from api.llm_service import get_text_embeddings_in_batches, get_text_embedding, generate_mcq_from_key_concepts as llm_generate_mcq_from_key_concepts
from api.syntext_agent import SyntextAgent
from api.rag_utils import rag_pipeline
import stripe
from api.websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import HTTPException
import gc
from typing import Optional, List, Dict, Any
from api.models.async_db import get_database_url
from api.processors.factory import FileProcessingFactory
import numpy as np  # Added for distractor generation
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Load Whisper model once at startup
whisper_model = None  # Initialize as None, will be loaded by load_whisper_model

def load_whisper_model_if_needed():
    global whisper_model
    if whisper_model is None:
        try:
            logger.info("Loading faster-whisper model...")
            # Use default model location (faster-whisper will handle downloading)
            whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
            logger.info("Faster-whisper model loaded.")
        except Exception as e:
            logger.warning(f"Failed to load small Whisper model: {e}")
            logger.info("Falling back to tiny model...")
            try:
                whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
                logger.info("Tiny Whisper model loaded as fallback.")
            except Exception as tiny_e:
                logger.error(f"Failed to load tiny Whisper model: {tiny_e}")
                whisper_model = None
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
    if model is None:
        logger.error(f"Whisper model failed to load, cannot transcribe {file_path}")
        return [], None

    transcribe_params = {
        'language': language if language else None,  # None for auto-detect, or specify lang code
        'beam_size': 5,
        'vad_filter': False,  # Disable VAD since YouTube API fallback works perfectly
    }
    logger.info(f"Transcribing audio file {file_path} with params: {transcribe_params}")
    logger.info(f"Audio file size: {os.path.getsize(file_path)} bytes")

    try:
        # Run the blocking transcribe call in a separate thread with timeout
        logger.info(f"Starting transcription thread for {file_path}")
        segments_generator, info = await asyncio.wait_for(
            asyncio.to_thread(model.transcribe, file_path, **transcribe_params),
            timeout=300  # 5 minutes should be enough for most videos
        )

        # Convert generator to list of segments with progress logging
        processed_segments = []
        segments_list = list(segments_generator)  # Convert to list first
        logger.info(f"Starting to process {len(segments_list)} segments...")

        for i, segment in enumerate(segments_list):
            processed_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })
            if i % 10 == 0:  # Log progress every 10 segments
                logger.info(f"Processed {i+1}/{len(segments_list)} segments so far...")

        logger.info(f"Transcription successful for {file_path}. Detected language: {info.language}, Confidence: {info.language_probability}")
        return processed_segments, info
    except asyncio.TimeoutError:
        logger.error(f"Transcription timed out after 5 minutes for {file_path}")
        return [], None
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
    """Converts Whisper segments to the format expected by downstream processing (dicts like PDF)."""
    transcript_data = []
    for segment in whisper_segments:
        start_time = float(segment['start'])
        end_time = float(segment['end'])
        duration = end_time - start_time
        transcript_data.append({
            'start': start_time,
            'duration': duration,
            'text': segment['text']
        })
    return transcript_data

async def handle_processing_error(file_id: int, error_msg: str) -> dict:
    """Handle errors during file processing by updating status and logging."""
    error_msg = error_msg[:1000]  # Truncate for consistency
    logger.error(error_msg)
    try:
        await store.file_repo.update_file_status(file_id, "failed")
    except Exception as db_error:
        logger.error(f"Failed to update file status to failed for {file_id}: {db_error}")
    return {"success": False, "error_message": error_msg}

async def process_file_data(
    user_gc_id: str,
    file_id: str,
    user_id: str,
    filename: str,
    file_url: str,
    is_youtube: bool = False,
    language: str = "English",
    comprehension_level: str = "Beginner"
):
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate key concepts, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id})")

    def _infer_user_gc_id_from_file_url(url: str) -> Optional[str]:
        """Infer firebase uid / GCS prefix from a public GCS URL.

        Expected patterns:
        - https://storage.googleapis.com/<bucket>/<user_gc_id>/<filename>
        - https://storage.googleapis.com/<bucket>/<user_gc_id>/...
        """
        try:
            if not url:
                return None
            parsed = urlparse(url)
            # Path looks like: /<bucket>/<user_gc_id>/<filename>
            parts = [p for p in (parsed.path or "").split("/") if p]
            if len(parts) < 2:
                return None
            inferred = parts[1]
            return inferred or None
        except Exception:
            return None

    async with store.file_repo.get_async_session() as transaction:  # Start transaction
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

            # Verify or create file record
            file = await store.file_repo.get_file_by_id(file_id_int)
            if not file:
                logger.info(f"File ID {file_id} not found, creating new file record")
                file_data = {
                    "user_id": int(user_id),
                    "filename": filename,
                    "file_type": FileUtils.determine_file_type(filename),
                    "status": "pending",
                    "url": file_url
                }
                file_result = await store.file_repo.create_file(file_data)
                if not file_result or not file_result.get('id'):
                    logger.error(f"Failed to create file record for file_id: {file_id}")
                    return await handle_processing_error(file_id_int, f"Failed to create file record for file_id: {file_id}")
                logger.info(f"Created file record for file_id: {file_id}")
                file = file_result

            # Update file type if not set
            if not file.get('file_type'):
                new_file_type = FileUtils.determine_file_type(filename)
                success = await store.file_repo.update_file_type(file_id_int, new_file_type)
                if not success:
                    return await handle_processing_error(file_id_int, f"Failed to update file type for file ID: {file_id}")

            # Check processing status
            if file.get('processing_status') == "processed":
                logger.info(f"File {file_id} is already processed")
                return {"success": True, "final_status": "processed", "error_message": None}

            # Update status to extracting
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
                file_data = None

                if file_url:
                    try:
                        logger.info(f"Downloading file {filename} from file_url")
                        resp = await asyncio.to_thread(requests.get, file_url, timeout=30)
                        if resp.ok and resp.content:
                            file_data = resp.content
                        else:
                            logger.warning(f"Failed to download file from file_url (status={resp.status_code})")
                    except Exception as e:
                        logger.warning(f"Error downloading file from file_url: {e}")

                if file_data is None:
                    logger.info(f"Downloading file {filename} from GCS")
                    if not user_gc_id:
                        inferred_gc_id = _infer_user_gc_id_from_file_url(file_url)
                        if inferred_gc_id:
                            logger.info(
                                "user_gc_id missing; inferred from file_url for GCS download"
                            )
                            user_gc_id = inferred_gc_id
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

async def process_query_data(
    id: str,
    history_id: str,
    message: str,
    language: str,
    comprehension_level: str,
    workspace_id: int | None = None,
    file_id: int | None = None,
):
    """Processes a user query and generates a response using SyntextAgent with enhanced RAG."""
    try:
        # Get conversation history in formatted form
        formatted_history = await store.user_repo.format_user_chat_history(history_id, id)
        
        # Try enhanced RAG pipeline first
        try:
            logger.info(f"Processing query with enhanced RAG: '{message}'")
            from api.rag_utils import process_query, hybrid_search, cross_encoder_rerank, smart_chunk_selection
            
            # Process and expand the query
            rewritten_query, expanded_terms = process_query(message, formatted_history)
            logger.info(f"Original query: '{message}', Rewritten: '{rewritten_query}'")
            if expanded_terms:
                logger.info(f"Query expansion terms: {', '.join(expanded_terms)}")
            
            # Generate embeddings for the query
            query_embedding = get_text_embedding(rewritten_query)
            
            # Enhanced retrieval: get more candidates for reranking via hybrid search
            vector_results = await store.file_repo.hybrid_search(
                user_id=int(id),
                query=rewritten_query,
                query_embedding=query_embedding,
                workspace_id=workspace_id,
                file_id=file_id,
                top_k=15,
            )
            
            # If we have expanded terms, try to get additional results and combine them
            additional_results = []
            if expanded_terms:
                for term in expanded_terms[:3]:  # Limit to top 3 expansion terms
                    try:
                        term_embedding = get_text_embedding(term)
                        term_results = await store.file_repo.hybrid_search(
                            user_id=int(id),
                            query=term,
                            query_embedding=term_embedding,
                            workspace_id=workspace_id,
                            file_id=file_id,
                            top_k=5,
                        )
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
                
            # Deduplicate results by segment_id when available, otherwise by chunk_id.
            # NOTE: hybrid_search returns segment_id as a top-level field.
            seen_keys = set()
            unique_results = []
            for result in all_results:
                seg_id = result.get('segment_id')
                chunk_id = result.get('chunk_id')
                file_id = result.get('file_id')

                dedup_key = (file_id, seg_id) if seg_id is not None else (file_id, chunk_id)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                unique_results.append(result)

            # Provide a normalized score field for downstream components.
            # SmartChunkSelector expects `similarity_score`.
            for r in unique_results:
                if 'similarity_score' in r:
                    continue
                if r.get('hybrid_score') is not None:
                    r['similarity_score'] = float(r.get('hybrid_score') or 0.0)
                else:
                    r['similarity_score'] = 0.0
            
            # Apply cross-encoder reranking
            try:
                reranked_results = cross_encoder_rerank(rewritten_query, unique_results, top_k=15)
                logger.info(f"Reranked {len(unique_results)} results to {len(reranked_results)} top results")
            except Exception as rerank_error:
                logger.warning(f"Reranking error: {rerank_error}, falling back to original ranking")
                reranked_results = unique_results[:15] if len(unique_results) > 15 else unique_results

            # Ensure reranked results expose similarity_score for chunk selection.
            for r in reranked_results:
                if r.get('rerank_score') is not None:
                    r['similarity_score'] = float(r.get('rerank_score') or 0.0)
                elif r.get('hybrid_score') is not None:
                    r['similarity_score'] = float(r.get('hybrid_score') or 0.0)
                else:
                    r['similarity_score'] = 0.0
            
            # Select chunks that fit token budget while maximizing relevance and diversity
            context_chunks = smart_chunk_selection(reranked_results, rewritten_query)

            try:
                if context_chunks:
                    logger.info(
                        "Selected context chunks: " + ", ".join(
                            [
                                f"file={c.get('file_name','?')} page={c.get('page_number','?')} chunk_id={c.get('chunk_id','?')} score={c.get('similarity_score',0):.4f}"
                                for c in context_chunks[:6]
                            ]
                        )
                    )
            except Exception:
                pass
            
            # Generate response using the enhanced context
            response = syntext.query_pipeline(message, formatted_history, context_chunks, language, comprehension_level)
            logger.info("Enhanced RAG pipeline completed successfully")
            
        except Exception as rag_error:
            # Fallback to original RAG pipeline if enhanced version fails
            logger.warning(f"Enhanced RAG pipeline failed: {rag_error}. Falling back to original pipeline.")
            query_embedding = get_text_embedding(message)
            topK_chunks = await store.file_repo.hybrid_search(
                user_id=int(id),
                query=message,
                query_embedding=query_embedding,
                workspace_id=workspace_id,
                file_id=file_id,
                top_k=10,
            )
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

async def generate_mcq_from_key_concepts(key_concepts: List[Dict[str, Any]], comprehension_level: str = "Beginner") -> List[Dict[str, Any]]:
    """
    Generate multiple choice questions from key concepts using LLM service.

    Args:
        key_concepts: List of key concept dictionaries
        comprehension_level: Target comprehension level (e.g., Beginner, Intermediate)

    Returns:
        List of MCQ dictionaries with question, options, and answer
    """
    if not key_concepts:
        logger.warning("No key concepts provided for MCQ generation")
        return []

    try:
        # Generate MCQs using LLM service (not async, returns list directly)
        mcqs = llm_generate_mcq_from_key_concepts(key_concepts, comprehension_level)

        if len(mcqs) == 0:
            logger.debug(
                f"LLM returned 0 MCQs for {len(key_concepts)} concept(s); applying heuristic fallback"
            )

            # Heuristic fallback to guarantee at least one MCQ
            concept = key_concepts[0]
            title = concept.get('concept_title') or concept.get('concept') or 'this concept'
            explanation = (concept.get('concept_explanation') or concept.get('explanation') or '').strip()

            # Build a simple definition question
            first_sentence = explanation.split('.')
            first_sentence = first_sentence[0].strip() if first_sentence and first_sentence[0].strip() else explanation
            if not first_sentence:
                first_sentence = 'a key idea discussed in this material'

            question = f"What best describes {title}?"
            correct = first_sentence

            # Collect distractors from other concepts if available
            other_concepts = [c for c in key_concepts[1:] if c is not concept]
            distractors: List[str] = []
            for oc in other_concepts:
                oc_exp = (oc.get('concept_explanation') or oc.get('explanation') or '').strip()
                oc_sent = oc_exp.split('.')
                oc_sent = oc_sent[0].strip() if oc_sent and oc_sent[0].strip() else oc_exp
                if oc_sent and oc_sent.lower() != correct.lower():
                    distractors.append(oc_sent)
                if len(distractors) >= 3:
                    break

            # If still not enough distractors, add generic plausible ones
            generic_pool = [
                "An unrelated historical fact not covered here",
                "A peripheral detail with no direct connection",
                "A common misconception about the topic",
                "A random example that does not apply in this context",
            ]
            for g in generic_pool:
                if len(distractors) >= 3:
                    break
                if g.lower() != correct.lower():
                    distractors.append(g)

            options = [correct] + distractors[:3]
            # Stable shuffle alternative without randomness (keep as is); correct is at index 0
            fallback_mcq = {
                'question': question,
                'options': options,
                'answer': correct,
            }
            logger.info("Heuristic MCQ fallback produced 1 MCQ")
            return [fallback_mcq]

        logger.info(f"Generated {len(mcqs)} MCQs from {len(key_concepts)} key concepts")
        return mcqs
    except Exception as e:
        logger.error(f"Error generating MCQs: {e}", exc_info=True)
        return []


async def generate_flashcards_from_key_concepts(key_concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate flashcards from key concepts using LLM service.

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


async def generate_mcqs_for_concepts_batch(
    concepts: List[Dict[str, Any]],
    comprehension_level: str = "Beginner",
    batch_size: int = 5,
) -> List[Dict[str, Any]]:
    """Generate MCQs for multiple concepts in a small number of LLM calls.

    Returns a flat list of MCQs, each including key_concept_id.
    """
    if not concepts:
        return []

    from api.llm_service import gradient_chat

    all_mcqs: List[Dict[str, Any]] = []
    for i in range(0, len(concepts), batch_size):
        batch = concepts[i:i + batch_size]
        # Keep only fields we need and ensure each has an id
        items = [
            {
                "key_concept_id": int(c["id"]),
                "concept_title": c.get("concept_title") or c.get("concept") or "",
                "concept_explanation": c.get("concept_explanation") or c.get("explanation") or "",
            }
            for c in batch
            if c.get("id") is not None
        ]
        if not items:
            continue

        prompt = (
            "You are an expert educator. Create exactly 1 multiple-choice question per concept.\n"
            f"Write for {comprehension_level} level.\n\n"
            "Output ONLY JSON: an array of objects with fields:\n"
            "- key_concept_id (integer, must match input)\n"
            "- question (string)\n"
            "- options (array of 4 strings)\n"
            "- answer (string; must be one of options)\n\n"
            "Concepts JSON:\n"
            f"{json.dumps(items)}\n\n"
            "JSON array:" 
        )

        raw = gradient_chat(prompt, max_tokens=1200)
        if not raw:
            continue

        raw = re.sub(r"```(?:json)?\n?", "", raw).strip("` \n")
        try:
            parsed = json.loads(raw)
        except Exception:
            # Try extracting a JSON array substring
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else []

        if isinstance(parsed, list):
            for mcq in parsed:
                if not isinstance(mcq, dict):
                    continue
                if mcq.get("key_concept_id") is None:
                    continue
                all_mcqs.append(mcq)

    return all_mcqs


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