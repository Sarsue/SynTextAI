import logging
import os
from tempfile import NamedTemporaryFile
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
store = DocSynthStore(DATABASE_URL)
syntext = SyntextAgent()

async def transcribe_audio_chunked(file_path: str, lang: str) -> list:
    """
    Transcribe audio using WhisperModel.
    """
    model_size = "medium"
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    try:
        if lang == 'en':
            segments, info = model.transcribe(file_path, beam_size=5, task="translate")
        else:
            segments, info = model.transcribe(file_path, beam_size=5)
        
        logger.info(f"Detected language '{info.language}' with probability {info.language_probability}")
        transcription_segments = [
            {
                "start_time": segment.start,
                "end_time": segment.end,
                "content": segment.text,
                "chunks": chunk_text(segment.text)
            }
            for segment in segments
        ]

        return transcription_segments

    except Exception as e:
        logger.exception("Error in transcription")
        raise HTTPException(status_code=500, detail="Transcription failed")

async def process_file_data(user_id: str, user_gc_id: str, filename: str, language: str):
    """
    Process a file (audio, video, or document) and store its data.
    """
    logger.info(f"Processing file: {filename} for user_id: {user_id}")
    file = download_from_gcs(user_gc_id, filename)

    try:
        if not file:
            raise FileNotFoundError(f"File not provided or not found.")
        
        # Use a temporary file for video processing
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.').lower()

        if ext in ["mp4", "mkv", "avi"]:
            logger.info("Extracting audio from video...")
            with NamedTemporaryFile(delete=True, suffix=os.path.splitext(filename)[1]) as temp_file:
                temp_file.write(file)
                temp_file_path = temp_file.name
                extracted_data = transcribe_audio_chunked(temp_file_path, lang=language) 
                ext = "video"
        else:
            # Good place to have the PDF-to-image logic for better extraction
            logger.info("Processing document file...")
            extracted_data = extract_data(file, ext)

        # Iterate over each page and process its chunks
        for data in extracted_data:
            page_chunks = data["chunks"]

            # Extract content of chunks for the current page
            chunk_contents = [chunk["content"] for chunk in page_chunks]

            # Generate embeddings for the current page's chunks
            chunk_embeddings = get_text_embeddings_in_batches(chunk_contents, batch_size=5)

            # Assign embeddings back to each chunk
            for chunk, embedding in zip(page_chunks, chunk_embeddings):
                chunk["embedding"] = embedding  # Attach the embedding to the chunk

        store.update_file_with_chunks(user_id, filename, ext, extracted_data)
  
        result = {'user_id': user_id, 'filename': filename, 'status': 'processed'}
    
        websocket_manager.send_message(user_id, "file_processed", {"status": "success", "result": result})
        
        return {"status": "success", "result": result}
    
    except ValueError as ve:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(ve)}
        logger.error(f"Error Validating {filename}: {str(result_data)}")
        websocket_manager.send_message(user_id, "file_processed", {"status": "failed", "error": str(ve)})
    
    except Exception as e:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(e)}
        logger.error(f"Error processing {filename}: {str(result_data)}")
        websocket_manager.send_message(user_id, "file_processed", {"status": "failed", "error": str(e)})

async def process_query_data(id: str, history_id: str, message: str, language: str, comprehension_level: str):
    """
    Process a user query and generate a response using SyntextAgent.
    """
    try:
        # Gather context for agent message history, top similar documents, and current query
        formatted_history = store.format_user_chat_history(history_id, id)
        topK_chunks = store.query_chunks_by_embedding(id, get_text_embedding(message))
        response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
        store.add_message(
            content=response, sender='bot', user_id=id, chat_history_id=history_id)
        
        websocket_manager.send_message(id, "message_received", {"status": "success", "history_id": history_id, "message": response})
        
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        
        websocket_manager.send_message(id, "message_received", {"status": "error", "error": str(e)})

async def delete_user_task(user_id: str, user_gc_id: str):
    """
    Delete a user's account, subscription, and associated files.
    """
    try:
        # Get the user's subscription details
        user_sub = store.get_subscription(user_id)

        if user_sub and user_sub["status"] == "active":
            user_stripe_sub = user_sub["stripe_subscription_id"]
            stripe_customer_id = user_sub.get("stripe_customer_id")

            # Cancel the subscription on Stripe
            stripe.Subscription.delete(user_stripe_sub)
            logger.info(f"Subscription {user_stripe_sub} canceled successfully.")

            # Now, remove the payment method from Stripe
            if stripe_customer_id:
                # Retrieve the payment method associated with the customer
                payment_methods = stripe.PaymentMethod.list(
                    customer=stripe_customer_id,
                    type="card"  # Assuming it's a card
                )

                # Detach and remove all payment methods associated with this customer
                for payment_method in payment_methods.auto_paging_iter():
                    # Detach the payment method (this removes it from the customer's account)
                    stripe.PaymentMethod.detach(payment_method.id)
                    logger.info(f"Payment method {payment_method.id} detached successfully.")
            
            # Optionally delete the customer if needed (be careful with this step)
            stripe.Customer.delete(stripe_customer_id)
            logger.info(f"Stripe customer {stripe_customer_id} deleted successfully.")
        
        # Delete the user's files from Google Cloud Storage
        files = store.get_files_for_user(user_id)   
        for f in files:
            delete_from_gcs(user_gc_id, f["name"])
        
        # Delete the user's account from the database
        store.delete_user_account(user_id)
        logger.info(f"User account {user_id} deleted successfully.")

    except Exception as e:
        logger.error(f"Error occurred during user deletion: {str(e)}")
        raise HTTPException(status_code=500, detail="User deletion failed")

# Example FastAPI route handlers
