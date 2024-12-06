from celery import shared_task
import os
import json
from dotenv import load_dotenv
import logging

from faster_whisper import WhisperModel  # Initialize whisper locally to avoid global initialization
from utils import format_timestamp, download_from_gcs
from tempfile import NamedTemporaryFile
from llm_service import syntext, chunk_text  # Import here to avoid circular imports
from doc_processor import process_file  # Ensures dependency is only imported when needed
from sqlite_store import DocSynthStore
from redis import StrictRedis, ConnectionPool  # Added connection pooling
from celery import current_app as celery
from flask_sse import sse

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


@shared_task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, file_path, lang):
    model_size = "medium"

    # Run on CPU with INT8 for simplicity
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    try:
        if lang == 'en':
            segments, info = model.transcribe(file_path, beam_size=5, task="translate")
        else:
            segments, info = model.transcribe(file_path, beam_size=5)
        
        logging.info(f"Detected language '{info.language}' with probability {info.language_probability}")
        return [segment.text for segment in segments]

    except Exception as e:
        logging.exception("Error in transcription")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))


@shared_task(bind=True)
def process_file_data(self, user_id, user_gc_id, filename, language, comprehension_level):
    # Ensure that the task is running within the Flask app context
    logging.info(f"Processing file: {filename} for user_id: {user_id}")
    file = download_from_gcs(user_gc_id, filename)
    store = DocSynthStore(os.getenv("DATABASE_PATH"))
    try:
            if not file:
                raise FileNotFoundError(f"File not provided or not found.")
              
            # Use a temporary file only for video data
            _, ext = os.path.splitext(filename)
            ext = ext.lstrip('.').lower()

            if ext in ["mp4", "mkv", "avi"]:
                logging.info("Extracting audio from video...")
                with NamedTemporaryFile(delete=True, suffix=os.path.splitext(filename)[1]) as temp_file:
                    temp_file.write(file)
                    temp_file_path = temp_file.name
                    transcriptions = transcribe_audio_chunked(temp_file_path, lang=None)  # Process the video file
                    transcription = " ".join(transcriptions)
            else:
                logging.info("Processing document file...")
                transcription = process_file(file, ext)  # Process document files directly

            # Interpret the transcription
            interpretations = []
            last_output = ""
            for content_chunk in chunk_text(transcription):
                interpretation = syntext(
                    content=content_chunk,
                    last_output=last_output,
                    intent='educate',
                    language=language,
                    comprehension_level=comprehension_level
                )
                interpretations.append(interpretation)
                last_output = interpretation

            logging.info(f"File processed successfully for user_id: {user_id}")
            store.update_file_with_extract(user_id, filename, transcription)
            result_message = "\n\n".join(interpretations)
            store.add_message(content=result_message, sender='bot', user_id=user_id)
            result = {'user_id': user_id, 'filename': filename, 'status': 'processed'}
            sse.publish({"message": "Task completed", "result": result}, type='task_update', channel=f"user_{user_id}")
            return {"status": "success", "result": result}
           
    except ValueError as ve:
            result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(ve)}
            logging.error(f"Error Validating {filename}: {str(result_data)}")
            sse.publish({"message": f"Task failed: {str(ve)}"}, type='task_error', channel=f"user_{user_id}")
        
          
          
    except Exception as e:
            result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(e)}
            logging.error(f"Error processing  {filename}: {str(result_data)}")
            sse.publish({"message": f"Task failed: {str(e)}"}, type='task_error', channel=f"user_{user_id}")
           
        

        