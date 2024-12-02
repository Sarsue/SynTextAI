from celery import Celery
import os
from dotenv import load_dotenv
import numpy as np
import logging
import mimetypes  # For MIME type validation
from faster_whisper import WhisperModel  # Initialize whisper locally to avoid global initialization
from utils import format_timestamp, download_from_gcs
from tempfile import NamedTemporaryFile
# Load environment variables
load_dotenv()

# Retrieve environment variables
redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

# Redis connection URL with SSL certificate verification
redis_url = f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_OPTIONAL'

def make_celery(app_name: str):
    celery = Celery(
        app_name,
        backend=redis_url,
        broker=redis_url,
    )
    celery.conf.update({
        'broker_url': redis_url,
        'result_backend': redis_url,
        'broker_transport_options': {
            'visibility_timeout': 3600,
            'socket_timeout': 30,
            'socket_connect_timeout': 10,
        },
        'task_time_limit': 900,
        'task_soft_time_limit': 600,
        'worker_prefetch_multiplier': 1,
        'broker_connection_retry': True,
        'broker_connection_max_retries': None,
    })
    celery.conf.task_routes = {
        'tasks.transcribe_audio_chunked': {'queue': 'video_audio_queue'},
        'tasks.process_file_data': {'queue': 'document_processing_queue'}
    }

    return celery

celery_app = make_celery('api')

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")



@celery_app.task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, file_path, lang):
    model_size = "medium"

    # Run on GPU with FP16
    #model = WhisperModel(model_size, device="cuda", compute_type="float16")

    # or run on GPU with INT8
    # model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
    # or run on CPU with INT8
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    srt_content = []
    try:
        if lang == 'en':
            segments, info = model.transcribe(file_path, beam_size=5, task="translate")
        else:
            segments, info = model.transcribe(file_path, beam_size=5)
        print("Detected language '%s' with probability %f" % (info.language, info.language_probability))

        return [segment.text for segment in segments]

    except Exception as e:
        logging.exception("Error in transcription")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))

@celery_app.task(bind=True)
def process_file_data(self, user_id, user_gc_id, filename, language, comprehension_level):
    from llm_service import syntext, chunk_text  # Import here to avoid circular imports
    from doc_processor import process_file  # Ensures dependency is only imported when needed

    logging.info(f"Processing file: {filename} for user_id: {user_id}")
    file = download_from_gcs(user_gc_id,filename)
    try:
        if not file:
            raise FileNotFoundError(f"File not provided or not found.")
          
        with NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
            temp_file.write(file)
            temp_file_path = temp_file.name


        # Validate file type
        # mime_type = validate_mime_type(file)
        # logging.info(f"File MIME type: {mime_type}")

        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.').lower()

        if ext in ["mp4", "mkv", "avi"]:
            logging.info("Extracting audio from video...")
            transcription = transcribe_audio_chunked(temp_file_path)
        else:
            logging.info("Processing document file...")
            transcription = process_file(file, ext)

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
        return {
            'user_id': user_id,
            'filename': file.filename,
            'transcription': transcription,
            'interpretations': interpretations
        }
    except ValueError as ve:
        logging.error(f"Validation error: {ve}")
        return {'error': str(ve)}
    except Exception as e:
        logging.exception(f"Error processing {file.filename}")
        return {'error': str(e)}
