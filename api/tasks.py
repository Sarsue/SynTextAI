import logging
import os
import tempfile
from contextlib import contextmanager
from text_extractor import extract_data
from faster_whisper import WhisperModel
from utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from docsynth_store import DocSynthStore
from llm_service import get_text_embeddings_in_batches, get_text_embedding, generate_explanation_dspy, token_count, MAX_TOKENS_CONTEXT, generate_summary_dspy
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
whisper_model = WhisperModel("medium", device="cpu", compute_type="int8", download_root="/app/models")

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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

store = DocSynthStore(database_url=DATABASE_URL)
syntext = SyntextAgent()

# Model is only loaded when needed to save memory
@contextmanager
def load_whisper_model():
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    try:
        yield model
    finally:
        del model  # Explicitly delete to free memory



async def transcribe_audio_chunked(file_path: str, lang: str) -> list:
    """Transcribes an audio file and ensures garbage collection."""
    try:
        transcribe_params = {"beam_size": 5}
        if lang == 'en':
            transcribe_params["task"] = "translate"

        segments, info = whisper_model.transcribe(file_path, **transcribe_params)
        logger.info(f"Detected language '{info.language}' with probability {info.language_probability}")

        transcribed_data = [
            {
                "start_time": segment.start,
                "end_time": segment.end,
                "content": segment.text,
                "chunks": chunk_text(segment.text),
            }
            for segment in segments
        ]

        # Force garbage collection
        gc.collect()

        return transcribed_data

    except Exception as e:
        logger.exception("Error in transcription")
        raise HTTPException(status_code=500, detail="Transcription failed")

async def process_file_data(user_gc_id: str, user_id: str, file_id: str, filename: str, file_url: str, is_youtube: bool = False, explanation_interval_seconds: Optional[int] = None):
    """Processes the uploaded file: download, extract/transcribe, generate embeddings, generate explanations, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id}, GCS_ID: {user_gc_id}, Interval: {explanation_interval_seconds})")
    # --- YOUTUBE LINK HANDLING ---
    if is_youtube or (filename.startswith('http') and ('youtube.com' in filename or 'youtu.be' in filename)):
        logger.info(f"Processing YouTube link: {filename}")
        try:
            from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
            import re
            # Extract video ID from URL
            yt_match = re.search(r'(?:v=|youtu.be/|embed/)([\w-]{11})', filename)
            video_id = yt_match.group(1) if yt_match else None
            if not video_id:
                logger.error(f"Could not extract video ID from YouTube URL: {filename}")
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = transcript_list.find_transcript(['en'])
                transcript_data = transcript.fetch()
            except (TranscriptsDisabled, NoTranscriptFound):
                logger.warning(f"No transcript available for YouTube video: {filename}")
                return
            except Exception as e:
                logger.error(f"Error fetching transcript for YouTube video: {filename}: {e}")
                return
            # Process transcript into segments
            processed_video_data = []
            all_small_chunks = [] # List to hold tuples of (segment_index, chunk_text)
            segment_chunk_map = {} # Map segment index to its list of small chunk texts
            for entry in transcript_data:
                segment = {
                    'start_time': entry.start,
                    'end_time': entry.start + entry.duration,
                    'content': entry.text,
                    'duration': entry.duration
                }
                processed_video_data.append(segment)
                all_small_chunks.append((len(processed_video_data)-1, entry.text))
                segment_chunk_map[len(processed_video_data)-1] = [entry.text]
            # --- Chunk, embed, explain, summarize (reuse video logic) ---
            # Generate embeddings for all small chunks across the video in one batch
            logger.info(f"Generating embeddings for {len(all_small_chunks)} small chunks across video...")
            all_small_chunk_texts = [text for _, text in all_small_chunks]
            all_embeddings = []
            if all_small_chunk_texts:
                all_embeddings = get_text_embeddings_in_batches(all_small_chunk_texts)
            embedding_dict = {all_small_chunks[i][1]: all_embeddings[i] for i in range(len(all_small_chunks))} if len(all_embeddings) == len(all_small_chunks) else {}
            # Attach embeddings to segments
            for i, segment in enumerate(processed_video_data):
                segment_chunks = segment_chunk_map.get(i, [])
                segment['chunks'] = []
                for small_chunk_text in segment_chunks:
                    embedding = embedding_dict.get(small_chunk_text) # Look up embedding
                    segment['chunks'].append({
                        'embedding': embedding,
                        'content': small_chunk_text
                    })
            # Explanations based on configurable intervals for YouTube videos
            if transcript_data: # Ensure we have transcript data
                # Determine interval: use parameter, else default to 30s. Ensure positive.
                interval_to_use = 30 # Default
                if explanation_interval_seconds is not None:
                    if explanation_interval_seconds > 0:
                        interval_to_use = explanation_interval_seconds
                    else:
                        logger.warning(f"Provided explanation_interval_seconds ({explanation_interval_seconds}) for file {file_id} is not positive. Defaulting to 30s.")
                
                logger.info(f"Using video explanation interval: {interval_to_use} seconds for file ID {file_id}")

                # Calculate total duration from the last transcript entry
                total_video_duration = transcript_data[-1].start + transcript_data[-1].duration

                for current_interval_start_sec in range(0, int(total_video_duration) + 1, interval_to_use):
                    interval_start_time = float(current_interval_start_sec)
                    interval_end_time = min(float(current_interval_start_sec + interval_to_use), total_video_duration)

                    if interval_start_time >= total_video_duration: # Avoid empty last interval if total_duration is a multiple of interval_seconds
                        break

                    # Collect text within this specific interval
                    text_for_interval = []
                    for entry in transcript_data:
                        entry_start_val = entry.start 
                        entry_duration_val = entry.duration
                        entry_text_val = entry.text
                        
                        entry_end_val = entry_start_val + entry_duration_val
                        # Check for overlap: max(A_start, B_start) < min(A_end, B_end)
                        if max(interval_start_time, entry_start_val) < min(interval_end_time, entry_end_val):
                            text_for_interval.append(entry_text_val)
                    
                    full_interval_text = " ".join(text_for_interval).strip()

                    if full_interval_text:
                        try:
                            logger.info(f"Generating explanation for video ID {file_id}, interval: {interval_start_time:.1f}s - {interval_end_time:.1f}s")
                            explanation_text_content = generate_explanation_dspy(full_interval_text) # Renamed variable
                            if explanation_text_content and not explanation_text_content.startswith("Error:"):
                                store.save_explanation(
                                    user_id=int(user_id),
                                    file_id=int(file_id),       # Ensuring file_id is int
                                    selection_type='video_range',
                                    explanation=explanation_text_content,
                                    video_start=interval_start_time,
                                    video_end=interval_end_time
                                )
                        except Exception as e:
                            logger.error(f"Error generating/saving explanation for YouTube video ID {file_id}, interval {interval_start_time:.1f}s-{interval_end_time:.1f}s: {e}")
                    
                    if interval_end_time >= total_video_duration:
                        break # Processed the whole video
            
            # Save chunks
            store.update_file_with_chunks(user_id=int(user_id), filename=filename, extracted_data=processed_video_data, public_url=filename)
            # Summary
            full_transcription = " ".join([seg.get('content', '') for seg in processed_video_data if seg.get('content', '').strip()])
            if full_transcription:
                try:
                    summary_text = generate_summary_dspy(full_transcription)
                    if summary_text and not summary_text.startswith("Error:"):
                        store.update_file_summary(user_id=int(user_id), file_name=filename, summary=summary_text)
                except Exception as summary_err:
                    logger.error(f"Error during summary generation/saving for YouTube video {filename}: {summary_err}")
            logger.info(f"Finished processing YouTube video: {filename}")
            return
        except Exception as e:
            logger.error(f"Error processing YouTube link {filename}: {e}")
            return
    # --- END YOUTUBE HANDLING ---
    file_path = None # Initialize file_path to None
    # Wrap entire process in a try-except block to catch errors
    try: # Main try block starts here
        # Use the global store instance, remove the re-initialization below
        # store = DocSynthStore() # REMOVE THIS LINE 
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

            # --- Generate and save explanations for video segments --- 
            logger.info(f"Generating explanations for {len(transcribed_data)} video segments...")
            for i, segment in enumerate(transcribed_data): # Corrected: use enumerate if needed, or just loop
                try:
                    segment_content = segment.get('content', '')
                    if not segment_content.strip():
                        logger.debug(f"Skipping empty video segment {segment.get('start_time', 0):.1f}s for explanation.")
                        continue

                    # Call the new DSPy-based explanation function
                    explanation_text = generate_explanation_dspy(segment_content)

                    if explanation_text and not explanation_text.startswith("Error:"): # Check for valid explanation
                        store.save_explanation(
                            user_id=int(user_id),
                            file_id=file_id,
                            selection_type='video_range',
                            explanation=explanation_text,
                            video_start=segment['start_time'],
                            video_end=segment['end_time']
                        )
                        logger.debug(f"Saved explanation for video segment {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")
                    else:
                        logger.warning(f"LLM failed to generate explanation for video segment {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")
                except Exception as e:
                    logger.error(f"Error generating/saving explanation for video segment {segment.get('start_time', 0):.1f}s: {e}", exc_info=True)
            logger.info("Finished generating video segment explanations.")

            # Save the segmented video data with embeddings
            if processed_video_data:
                store.update_file_with_chunks(user_id=int(user_id), filename=filename, file_type="video", extracted_data=processed_video_data)
                logger.info("Processed and stored video segments with chunks.")
            else:
                logger.warning("No processed video data with chunks generated, skipping storage update.")

            # --- Generate and Save Summary (Video/Audio) ---
            logger.info(f"Attempting to generate summary for video/audio file: {filename}")
            full_transcription = " ".join([seg.get('content', '') for seg in transcribed_data if seg.get('content', '').strip()])
            if full_transcription:
                try:
                    summary_text = generate_summary_dspy(full_transcription)
                    if summary_text and not summary_text.startswith("Error:"):
                        store.update_file_summary(user_id=int(user_id), file_name=filename, summary=summary_text)
                        logger.info(f"Successfully generated and saved summary for video/audio file: {filename}")
                    else:
                        logger.error(f"Failed to generate summary for video/audio {filename}: {summary_text}")
                except Exception as summary_err:
                    logger.error(f"Error during summary generation/saving for video/audio {filename}: {summary_err}", exc_info=True)
            else:
                logger.warning(f"No content found to summarize for video/audio file: {filename}")
            # --------------------------------------------------

        elif ext == "pdf":
            logger.info("Extracting text from PDF...")
            # Use pdf_extracter to get page-based data
            from pdf_extracter import extract_text_with_page_numbers 
            page_data = extract_text_with_page_numbers(file_data)
            logger.info(f"PDF extraction complete. Pages: {len(page_data)}")

            # --- Prepare data structure with embeddings for storage ---+
            processed_page_data = []
            logger.info(f"Chunking content for {len(page_data)} PDF pages...") # Consistency
            for page_item in page_data:
                try:
                    page_content = page_item['content']
                    page_num = page_item['page_num']
                    if page_content:
                        # Chunk the page content
                        text_chunks = chunk_text(page_content)
                        non_empty_chunks = [chunk['content'] for chunk in text_chunks if chunk['content'].strip()]
                        page_chunks_with_embeddings = []

                        if non_empty_chunks:
                            # Generate embeddings for all non-empty chunks in batch
                            chunk_embeddings = get_text_embeddings_in_batches(non_empty_chunks)

                            # Ensure we got the same number of embeddings as chunks
                            if len(chunk_embeddings) == len(non_empty_chunks):
                                for i, chunk_content in enumerate(non_empty_chunks):
                                    page_chunks_with_embeddings.append({
                                        'embedding': chunk_embeddings[i],
                                        'content': chunk_content # Optional: store chunk content too
                                    })
                            else:
                                logger.error(f"Mismatch between chunk count ({len(non_empty_chunks)}) and embedding count ({len(chunk_embeddings)}) for page {page_num}")
                                # Decide how to handle mismatch: skip page, store without embeddings, etc.?
                                # For now, let's log error and proceed without embeddings for this page
                                page_chunks_with_embeddings = [] # Clear potentially partial data

                        # Structure data as expected by update_file_with_chunks
                        structured_item = {
                            'page_num': page_num,
                            'content': page_content, # Keep full page content for Segment record
                            'chunks': page_chunks_with_embeddings # List of chunk dicts
                        }
                        processed_page_data.append(structured_item)
                    else:
                        logger.warning(f"Skipping chunking/embedding for empty page {page_num}")
                except Exception as embed_error:
                    logger.error(f"Error chunking/embedding page {page_item.get('page_num', 'N/A')}: {embed_error}", exc_info=True)
            logger.info("Finished chunking and generating PDF page embeddings.")

            # --- Generate and save explanations for PDF pages --- 
            logger.info(f"Generating explanations for {len(page_data)} PDF pages...")
            for page_item in page_data: # Iterate original page_data for explanations
                try:
                    page_content = page_item.get('content', '')
                    page_num = page_item.get('page_num', 'N/A') # Get page number safely
                    if not page_content.strip():
                        logger.debug(f"Skipping empty PDF page {page_num} for explanation.")
                        continue
                        
                    # Call the new DSPy-based explanation function
                    explanation_text = generate_explanation_dspy(page_content)

                    if explanation_text and not explanation_text.startswith("Error:"):
                        store.save_explanation(
                            user_id=int(user_id),
                            file_id=file_id,
                            selection_type='page', # Changed to 'page' for clarity
                            explanation=explanation_text,
                            page=page_item['page_num'],
                        )
                        logger.debug(f"Saved explanation for PDF page {page_item['page_num']}")
                    else:
                        logger.warning(f"LLM failed to generate explanation for PDF page {page_item['page_num']}")
                except Exception as e:
                    logger.error(f"Error generating/saving explanation for PDF page {page_item['page_num']}: {e}", exc_info=True)
            logger.info("Finished generating PDF page explanations.")

            # Save the segmented PDF data with embeddings
            if processed_page_data:
                store.update_file_with_chunks(user_id=int(user_id), filename=filename, file_type="pdf", extracted_data=processed_page_data)
                logger.info("Processed and stored PDF segments.")
            else:
                logger.warning("No processed PDF data with chunks generated, skipping storage update.")

            # --- Generate and Save Summary (PDF) ---
            logger.info(f"Attempting to generate summary for PDF file: {filename}")
            full_pdf_text = " ".join([page.get('content', '') for page in page_data if page.get('content', '').strip()])
            if full_pdf_text:
                try:
                    summary_text = generate_summary_dspy(full_pdf_text)
                    if summary_text and not summary_text.startswith("Error:"):
                        store.update_file_summary(user_id=int(user_id), file_name=filename, summary=summary_text)
                        logger.info(f"Successfully generated and saved summary for PDF file: {filename}")
                    else:
                        logger.error(f"Failed to generate summary for PDF {filename}: {summary_text}")
                except Exception as summary_err:
                    logger.error(f"Error during summary generation/saving for PDF {filename}: {summary_err}", exc_info=True)
            else:
                logger.warning(f"No content found to summarize for PDF file: {filename}")
            # -----------------------------------------

        else:
            logger.warning(f"Unsupported file type: {ext}")

    except Exception as e:
        logger.error(f"Error processing file {filename} (ID: {file_id}): {e}", exc_info=True)

    finally:
        # Check if file_path exists and is not None before removing
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temporary file: {file_path}")
        # Check if store exists before closing
        if store is not None:
            store.close_session() # Ensure session is closed
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
    """Processes a user query and generates a response using SyntextAgent."""
    try:
        formatted_history = store.format_user_chat_history(history_id, id)
        # --- Increase top_k for retrieval ---
        topK_chunks = store.query_chunks_by_embedding(id, get_text_embedding(message), top_k=10)
        response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
        
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
