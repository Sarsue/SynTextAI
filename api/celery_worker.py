from celery import Celery
import os
from doc_processor import process_file
import io
import numpy as np
import ffmpeg
import whisper
import logging
import base64
from llm_service import syntext, chunk_text

video_extensions = ["mp4", "mkv", "avi", "mov",
                    "wmv", "flv", "webm", "mpeg", "mpg", "3gp"]

model = whisper.load_model("base")

# Retrieve environment variables
redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

# Redis connection URL
redis_url = f'rediss://:{redis_pwd}@{redis_host}:{redis_port}/0?ssl_cert_reqs=CERT_NONE'

def make_celery(app_name: str):
    """Creates and configures the Celery application."""
    celery = Celery(
        app_name,
        backend=redis_url,
        broker=redis_url,
    )
    # Configuration options for the Celery worker
    celery.conf.update({
        'broker_url': redis_url,
        'result_backend': redis_url,
        'broker_transport_options': {
            'visibility_timeout': 3600,  # 1 hour timeout
            'socket_timeout': 30,
            'socket_connect_timeout': 10,
        },
        'task_time_limit': 900,  # Maximum task execution time
        'task_soft_time_limit': 600,  # Grace period before termination
        'worker_prefetch_multiplier': 1,  # Prevent task prefetching
        'broker_connection_retry': True,
        'broker_connection_max_retries': None,  # Infinite retries
    })

    return celery

# Create Celery app
celery_app = make_celery('api')

# Define tasks here
# Audio extraction and transcription tasks
@celery_app.task(bind=True, max_retries=5)
def extract_audio_to_memory_chunked(self, video_data, chunk_size=5):
    try:
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
        logging.error(f"Error extracting audio: {e}")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))

@celery_app.task(bind=True, max_retries=5)
def transcribe_audio_chunked(self, audio_chunks):
    try:
        full_transcription = ""
        for chunk in audio_chunks:
            try:
                decoded_audio = base64.b64decode(chunk)
                audio_array = np.frombuffer(decoded_audio, dtype=np.int16)

                if audio_array.size == 0:
                    logging.warning("Empty audio chunk detected. Skipping.")
                    continue

                # Use Whisper for transcription
                result = model.transcribe(audio_array)
                full_transcription += result["text"] + " "
            except Exception as e:
                logging.error(f"Error transcribing chunk: {e}")
                continue

        return full_transcription.strip()
    except Exception as e:
        logging.error(f"Error in transcription: {e}")
        raise self.retry(exc=e, countdown=min(2 ** self.request.retries, 300))

# File processing and storage
@celery_app.task(bind=True)
def process_file_data(self, user_id, user_gc_id, file, language, comprehension_level):
    logging.info(
        f"Starting to process file: {file.filename} for user_id: {user_id}")
    try:
     
        if not file:
            raise FileNotFoundError(f"{file.filename} not found.")

        _, ext = os.path.splitext(file.filename)
        ext = ext.lstrip('.').lower()

        if ext in video_extensions:
            audio_chunks = extract_audio_to_memory_chunked(file)
            transcription = transcribe_audio_chunked(audio_chunks)
        else:
            transcription = process_file(file, ext)

        interpretations = []
        last_output = ''
        for content_chunk in chunk_text(transcription):
            interpretation = syntext(content=content_chunk, last_output=last_output,
                                      intent='educate', language=language, comprehension_level=comprehension_level)
            interpretations.append(interpretation)
            last_output = interpretation

        result_message = "\n\n".join(interpretations)

        return {
            'user_id': user_id,
            'filename': file.filename,
            'transcription': transcription,
            'interpretations': interpretations
        }
    except Exception as e:
        logging.error(f"Error processing {file.filename}: {e}")
        return {'error': str(e)}
