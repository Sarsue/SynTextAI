from celery import Celery
import os
from dotenv import load_dotenv
import io
import base64
import numpy as np
import ffmpeg
import logging
import mimetypes  # For MIME type validation

# Load environment variables
load_dotenv()

# Retrieve environment variables
redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

# Redis connection URL
redis_url = f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE'


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
    return celery


celery_app = make_celery('api')

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def validate_mime_type(file):
    """Validates the MIME type of the uploaded file."""
    mime_type, _ = mimetypes.guess_type(file.filename)
    if mime_type is None or mime_type.split('/')[0] not in ['video', 'audio', 'text', 'application']:
        raise ValueError(f"Unsupported file type: {file.filename} with MIME type {mime_type}")
    return mime_type


@celery_app.task(bind=True, max_retries=5)
def extract_audio_to_memory_chunked(self, video_data, chunk_size=5):
    try:
        # Process audio extraction
        audio_stream, _ = (
            ffmpeg
            .input('pipe:', format='mp4')
            .output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar='16000')
            .run(input=video_data, capture_stdout=True, capture_stderr=True, timeout=300)
        )

        def stream_audio_chunks(audio_stream, chunk_size):
            stream = io.BytesIO(audio_stream)
            while True:
                chunk = stream.read(chunk_size * 1024 * 1024)
                if not chunk:
                    break
                yield base64.b64encode(chunk).decode('utf-8')

        return list(stream_audio_chunks(audio_stream, chunk_size))
    except Exception as e:
        logging.exception("Error extracting audio")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))


@celery_app.task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, audio_chunks):
    from whisper import load_model  # Initialize whisper locally to avoid global initialization
    model = load_model("base")

    try:
        full_transcription = ""
        for chunk in audio_chunks:
            decoded_audio = base64.b64decode(chunk)
            audio_array = np.frombuffer(decoded_audio, dtype=np.int16)

            if audio_array.size == 0:
                logging.warning("Empty audio chunk detected. Skipping.")
                continue

            result = model.transcribe(audio_array)
            full_transcription += result["text"] + " "

        return full_transcription.strip()
    except Exception as e:
        logging.exception("Error in transcription")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))


@celery_app.task(bind=True)
def process_file_data(self, user_id, user_gc_id, file, language, comprehension_level):
    from llm_service import syntext, chunk_text  # Import here to avoid circular imports
    from doc_processor import process_file  # Ensures dependency is only imported when needed

    logging.info(f"Processing file: {file.filename} for user_id: {user_id}")
    try:
        if not file:
            raise FileNotFoundError(f"File not provided or not found.")

        # Validate file type
        mime_type = validate_mime_type(file)
        logging.info(f"File MIME type: {mime_type}")

        _, ext = os.path.splitext(file.filename)
        ext = ext.lstrip('.').lower()

        if mime_type.startswith("video") or ext in ["mp4", "mkv", "avi"]:
            logging.info("Extracting audio from video...")
            video_data = file.read()
            audio_chunks = extract_audio_to_memory_chunked(video_data)
            transcription = transcribe_audio_chunked(audio_chunks)
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
