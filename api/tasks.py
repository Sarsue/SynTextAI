from celery import shared_task
from flask import current_app
import logging
import os
from tempfile import NamedTemporaryFile
from llm_service import syntext, chunk_text
from visual_data_scraper import process_data
from faster_whisper import WhisperModel
from utils import format_timestamp, download_from_gcs
import json
from docsynth_store import DocSynthStore
# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Construct paths relative to the base directory
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
@shared_task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, file_path, lang):
    model_size = "medium"
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
    logging.info(f"Processing file: {filename} for user_id: {user_id}")
    store = DocSynthStore(database_url=DATABASE_URL)
    file = download_from_gcs(user_gc_id, filename)

    try:
        if not file:
            raise FileNotFoundError(f"File not provided or not found.")
        
        # Use a temporary file for video processing
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.').lower()

        if ext in ["mp4", "mkv", "avi"]:
            logging.info("Extracting audio from video...")
            with NamedTemporaryFile(delete=True, suffix=os.path.splitext(filename)[1]) as temp_file:
                temp_file.write(file)
                temp_file_path = temp_file.name
                process_results = transcribe_audio_chunked(temp_file_path, lang=None)
                extracted_data = " ".join(process_results)
        else:
            logging.info("Processing document file...")
            extracted_data = process_data(file, ext)

        # Interpret the transcription
        llm_responses = []
        last_output = ""
        for content_chunk in chunk_text(extracted_data):
            llm_response = syntext(
                content=content_chunk,
                last_output=last_output,
                intent='educate',
                language=language,
                comprehension_level=comprehension_level
            )
            llm_responses.append(llm_response)
            last_output = llm_response

        logging.info(f"File processed successfully for user_id: {user_id}")
        store.update_file_with_extract(user_id, filename, extracted_data)
        result_message = "\n\n".join(llm_responses)
        store.add_message(content=result_message, sender='bot', user_id=user_id)
        
  
        result = {'user_id': user_id, 'filename': filename, 'status': 'processed'}
        return {"status": "success", "result": result}
    
    except ValueError as ve:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(ve)}
        logging.error(f"Error Validating {filename}: {str(result_data)}")
    
    except Exception as e:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(e)}
        logging.error(f"Error processing {filename}: {str(result_data)}")