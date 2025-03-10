import logging
import os
import tempfile
from contextlib import contextmanager
from text_extractor import extract_data
from faster_whisper import WhisperModel
from utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from docsynth_store import DocSynthStore
from llm_service import get_text_embeddings_in_batches, get_text_embedding
from syntext_agent import SyntextAgent
import stripe
from websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import BackgroundTasks, HTTPException

# Load environment variables
load_dotenv()

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
    """Transcribes an audio file in chunks using WhisperModel."""
    try:
        with load_whisper_model() as model:
            transcribe_params = {"beam_size": 5}
            if lang == 'en':
                transcribe_params["task"] = "translate"

            segments, info = model.transcribe(file_path, **transcribe_params)
            logger.info(f"Detected language '{info.language}' with probability {info.language_probability}")

            return [
                {
                    "start_time": segment.start,
                    "end_time": segment.end,
                    "content": segment.text,
                    "chunks": chunk_text(segment.text),
                }
                for segment in segments
            ]
    except Exception as e:
        logger.exception("Error in transcription")
        raise HTTPException(status_code=500, detail="Transcription failed")

async def process_file_data(user_id: str, user_gc_id: str, filename: str, language: str):
    """Processes a file (audio, video, or document) and stores its data."""
    logger.info(f"Processing file: {filename} for user_id: {user_id}")

    file_data = download_from_gcs(user_gc_id, filename)
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found.")

    _, ext = os.path.splitext(filename)
    ext = ext.lstrip('.').lower()

    try:
        if ext in {"mp4", "mkv", "avi"}:
            logger.info("Extracting audio from video...")
            with tempfile.NamedTemporaryFile(delete=True, suffix=f".{ext}") as temp_file:
                temp_file.write(file_data)
                temp_file.flush()
                extracted_data = await transcribe_audio_chunked(temp_file.name, lang=language)
                ext = "video"
        else:
            logger.info("Processing document file...")
            extracted_data = extract_data(file_data, ext)

        # Process and embed data in batches
        for data in extracted_data:
            chunk_contents = [chunk["content"] for chunk in data["chunks"]]
            chunk_embeddings = get_text_embeddings_in_batches(chunk_contents, batch_size=5)

            for chunk, embedding in zip(data["chunks"], chunk_embeddings):
                chunk["embedding"] = embedding

        store.update_file_with_chunks(user_id, filename, ext, extracted_data)

        response_data = {"user_id": user_id, "filename": filename, "status": "processed"}
        await websocket_manager.send_message(user_id, "file_processed", {"status": "success", "result": response_data})
        return {"status": "success", "result": response_data}

    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")
        await websocket_manager.send_message(user_id, "file_processed", {"status": "failed", "error": str(e)})
        raise HTTPException(status_code=500, detail="File processing failed")

async def process_query_data(id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """Processes a user query and generates a response using SyntextAgent."""
    try:
        formatted_history = store.format_user_chat_history(history_id, id)
        topK_chunks = store.query_chunks_by_embedding(id, get_text_embedding(message))
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
