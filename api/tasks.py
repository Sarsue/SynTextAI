from celery import shared_task
from flask import current_app
import logging
import os
from tempfile import NamedTemporaryFile
from text_extractor import extract_data
from faster_whisper import WhisperModel
from utils import format_timestamp, download_from_gcs
import json
from docsynth_store import DocSynthStore
from llm_service import get_text_embeddings_in_batches, get_text_embedding
from syntext_agent import SyntextAgent
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
store = DocSynthStore(database_url=DATABASE_URL)
syntext = SyntextAgent()

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
        transcription_chunks = [
            {
                "start_time": segment.start,
                "end_time": segment.end,
                "content": segment.text
            }
            for segment in segments
        ]

        return transcription_chunks

    except Exception as e:
        logging.exception("Error in transcription")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))


@shared_task(bind=True)
def process_file_data(self, user_id, user_gc_id, filename, language):
    logging.info(f"Processing file: {filename} for user_id: {user_id}")
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
                extracted_data = transcribe_audio_chunked(temp_file_path, lang=language) 
                ext = "video"
        else:
            # good place to have the pdf to image for better extraction logic
            logging.info("Processing document file...")
            extracted_data = extract_data(file, ext)

  
        logging.info(f"File processed successfully for user_id: {user_id} with {len(extracted_data)} chunks")
        chunk_content = [extracted_data_point['content'] for extracted_data_point in extracted_data]
        extracted_data_embeddings =  get_text_embeddings_in_batches(chunk_content, batch_size=5)
        store.update_file_with_chunks(user_id, filename, ext, extracted_data, extracted_data_embeddings)
  
        result = {'user_id': user_id, 'filename': filename, 'status': 'processed'}
        return {"status": "success", "result": result}
    
    except ValueError as ve:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(ve)}
        logging.error(f"Error Validating {filename}: {str(result_data)}")
    
    except Exception as e:
        result_data = {'user_id': user_id, 'filename': filename, 'status': 'failed', 'error': str(e)}
        logging.error(f"Error processing {filename}: {str(result_data)}")


@shared_task(bind=True)
def process_query_data(self, id, history_id, message, language):
    # Gather context for agent message history , top similar doocuments and current query
    formatted_history = store.format_user_chat_history(history_id, id)
    topK_chunks = store.query_chunks_by_embedding(id,get_text_embedding(message))
    response = syntext.query_pipeline(message,formatted_history,topK_chunks,language)
    store.add_message(
        content=response, sender='bot', user_id=id, chat_history_id=history_id)
  

