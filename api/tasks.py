from celery import shared_task
from flask import current_app
import logging
import os
from tempfile import NamedTemporaryFile
from text_extractor import extract_data
from faster_whisper import WhisperModel
from utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs, notify_user
import json
from docsynth_store import DocSynthStore
from llm_service import get_text_embeddings_in_batches, get_text_embedding
from syntext_agent import SyntextAgent
import stripe

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Stripe setup (move to app config in production)
stripe.api_key = os.getenv('STRIPE_SECRET')

@shared_task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, file_path, lang):
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
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))

@shared_task(bind=True)
def process_file_data(self, user_id, user_gc_id, filename, language):
    logger.info(f"Processing file: {filename} for user_id: {user_id}")
    
    # Use app context to access store and socketio
    with current_app.app_context():
        store = DocSynthStore(database_url=current_app.config['DATABASE_URL'])
        file = download_from_gcs(user_gc_id, filename)

        try:
            if not file:
                raise FileNotFoundError(f"File not provided or not found.")
            
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
                logger.info("Processing document file...")
                extracted_data = extract_data(file, ext)

            for data in extracted_data:
                page_chunks = data["chunks"]
                chunk_contents = [chunk["content"] for chunk in page_chunks]
                chunk_embeddings = get_text_embeddings_in_batches(chunk_contents, batch_size=5)
                for chunk, embedding in zip(page_chunks, chunk_embeddings):
                    chunk["embedding"] = embedding

            store.update_file_with_chunks(user_id, filename, ext, extracted_data)
            
            # Notify user
            notify_user(current_app.socketio, user_id, 'file_processed', {
                'filename': filename,
                'status': 'success'
            })
            
            result = {'user_id': user_id, 'filename': filename, 'status': 'processed'}
            return {"status": "success", "result": result}
        
        except ValueError as ve:
            result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(ve)}
            logger.error(f"Error Validating {filename}: {str(result_data)}")
            return result_data
        
        except Exception as e:
            notify_user(current_app.socketio, user_id, 'file_processed', {
                'filename': filename,
                'status': 'error',
                'error': str(e)
            })
            raise

@shared_task(bind=True)
def process_query_data(self, id, history_id, message, language, comprehension_level):
    with current_app.app_context():
        store = DocSynthStore(database_url=current_app.config['DATABASE_URL'])
        syntext = SyntextAgent()
        
        formatted_history = store.format_user_chat_history(history_id, id)
        topK_chunks = store.query_chunks_by_embedding(id, get_text_embedding(message))
        response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
        store.add_message(content=response, sender='bot', user_id=id, chat_history_id=history_id)
        
        # Notify user (fixed typo)
        notify_user(current_app.socketio, id, 'message_received', {
            'history_id': history_id,
            'message': response,
            'sender': 'bot'
        })

@shared_task(bind=True)
def delete_user_task(self, user_id, user_gc_id):
    with current_app.app_context():
        store = DocSynthStore(database_url=current_app.config['DATABASE_URL'])
        
        try:
            user_sub = store.get_subscription(user_id)
            if user_sub and user_sub["status"] == "active":
                user_stripe_sub = user_sub["stripe_subscription_id"]
                stripe_customer_id = user_sub.get("stripe_customer_id")

                stripe.Subscription.delete(user_stripe_sub)
                logger.info(f"Subscription {user_stripe_sub} canceled successfully.")

                if stripe_customer_id:
                    payment_methods = stripe.PaymentMethod.list(customer=stripe_customer_id, type="card")
                    for payment_method in payment_methods.auto_paging_iter():
                        stripe.PaymentMethod.detach(payment_method.id)
                        logger.info(f"Payment method {payment_method.id} detached successfully.")
                    stripe.Customer.delete(stripe_customer_id)
                    logger.info(f"Stripe customer {stripe_customer_id} deleted successfully.")
            
            files = store.get_files_for_user(user_id)
            for f in files:
                delete_from_gcs(user_gc_id, f["name"])
            
            store.delete_user_account(user_id)
            logger.info(f"User account {user_id} deleted successfully.")
            
            notify_user(current_app.socketio, user_id, 'account_deleted', {
                'status': 'success',
                'message': f"Account {user_id} deleted"
            })
        
        except Exception as e:
            logger.error(f"Error occurred during user deletion: {str(e)}")
            self.retry(exc=e)