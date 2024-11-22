import os
import logging
import base64
from flask import Blueprint, request, jsonify, current_app
from google.cloud import storage
from redis.exceptions import RedisError
from celery import chain
from celery_worker import celery_app
from sqlite_store import DocSynthStore
from llm_service import syntext, chunk_text
from utils import get_user_id
from doc_processor import process_file
import io
import numpy as np
import ffmpeg
import whisper

video_extensions = ["mp4", "mkv", "avi", "mov",
                    "wmv", "flv", "webm", "mpeg", "mpg", "3gp"]
model = whisper.load_model("base")

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')

bucket_name = 'docsynth-fbb02.appspot.com'
files_bp = Blueprint("files", __name__, url_prefix="/api/v1/files")
store = DocSynthStore(os.getenv("DATABASE_PATH"))

# Helper function to authenticate user and retrieve user ID


def authenticate_user():
    try:
        token = request.headers.get('Authorization')
        if not token:
            logging.error("Missing Authorization token")
            return None, None

        success, user_info = get_user_id(token)
        if not success:
            logging.error("Failed to authenticate user with token")
            return None, None

        user_id = current_app.store.get_user_id_from_email(user_info['email'])
        if not user_id:
            logging.error(f"No user ID found for email: {user_info['email']}")
            return None, user_info['user_id']

        logging.info(
            f"Authenticated user_id: {user_id}, user_gc_id: {user_info['user_id']}")
        return user_id, user_info['user_id']

    except Exception as e:
        logging.exception("Error during user authentication")
        return None, None

# Helper functions for GCS operations


def upload_to_gcs(file_data, user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        blob.upload_from_file(file_data, content_type=file_data.mimetype)
        blob.make_public()
        logging.info(f"Uploaded {filename} to GCS: {blob.public_url}")
        return blob.public_url
    except Exception as e:
        logging.error("Error uploading to GCS")
        return None


def download_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        return blob.download_as_bytes()
    except Exception as e:
        logging.error("Error downloading from GCS")
        return None


def delete_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        blob.delete()
        logging.info(f"Deleted {filename} from GCS.")
    except Exception as e:
        logging.error("Error deleting from GCS")

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
def store_video_transcription_result(result, user_id, filename, language, comprehension_level):
    logging.info(f"Storing video transcription result for {filename}")
    try:
        if result:
            store.update_file_with_extract(user_id, filename, result)
            interpretations = []
            last_output = ''
            for content_chunk in chunk_text(result):
                interpretation = syntext(content=content_chunk, last_output=last_output,
                                         intent='educate', language=language, comprehension_level=comprehension_level)
                interpretations.append(interpretation)
                last_output = interpretation

            result_message = "\n\n".join(interpretations)
            store.add_message(content=result_message,
                              sender='bot', user_id=user_id)
            logging.info(f"Processed and stored '{filename}' successfully.")
        else:
            logging.error(f"Failed to transcribe video for {filename}.")
            return {'error': 'Failed to transcribe video'}
    except Exception as e:
        logging.error(
            f"Error storing video transcription result for {filename}: {e}")
        return {'error': str(e)}


@celery_app.task(bind=True)
def process_and_store_file(user_id, user_gc_id, filename, language, comprehension_level):
    logging.info(
        f"Starting to process file: {filename} for user_id: {user_id}")
    try:
        file_data = download_from_gcs(user_gc_id, filename)
        if not file_data:
            raise FileNotFoundError(f"{filename} not found.")

        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.').lower()

        if ext in video_extensions:
            video_task = chain(
                extract_audio_to_memory_chunked.s(file_data),
                transcribe_audio_chunked.s(),
                store_video_transcription_result.s(
                    user_id, filename, language, comprehension_level)
            ).apply_async()
            logging.info(
                f"Video processing task for {filename} has been enqueued.")
            return jsonify({'status': 'processing', 'task_id': video_task.id}), 202

        else:
            result = process_file(file_data, ext)
            store.update_file_with_extract(user_id, filename, result)
            interpretations = []
            last_output = ''
            for content_chunk in chunk_text(result):
                interpretation = syntext(content=content_chunk, last_output=last_output,
                                         intent='educate', language=language, comprehension_level=comprehension_level)
                interpretations.append(interpretation)
                last_output = interpretation
            result_message = "\n\n".join(interpretations)
            store.add_message(content=result_message,
                              sender='bot', user_id=user_id)
            logging.info(f"Processed and stored '{filename}' successfully.")
    except Exception as e:
        logging.error(f"Error processing {filename}: {e}")
        return {'error': str(e)}

# Route to save file


@files_bp.route('', methods=['POST'])
def save_file():
    try:
        language = request.args.get('language', 'English')
        comprehension_level = request.args.get('comprehensionLevel', 'dropout')

        user_id, user_gc_id = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        if not request.files:
            logging.warning('No files provided')
            return jsonify({'error': 'No files provided'}), 400

        for _, file in request.files.items():
            file_url = upload_to_gcs(file, user_gc_id, file.filename)
            if not file_url:
                logging.error(f"Failed to upload {file.filename} to GCS")
                return jsonify({'error': 'File upload failed'}), 500

            store.add_file(user_id, file.filename, file_url)
            process_and_store_file.apply_async(
                (user_id, user_gc_id, file.filename, language, comprehension_level)
            )
            logging.info(f"Enqueued processing for {file.filename}")

        return jsonify({'message': 'File processing queued.'}), 202
    except RedisError as e:
        logging.error("Redis error")
        return jsonify({'error': 'Failed to enqueue job'}), 500
    except Exception as e:
        logging.error("Exception occurred")
        return jsonify({'error': str(e)}), 500

# Route to retrieve files


@files_bp.route('', methods=['GET'])
def retrieve_files():
    try:
        user_id, _ = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        files = store.get_files_for_user(user_id)
        return jsonify(files)
    except Exception as e:
        logging.error(f"Error retrieving files: {e}")
        return jsonify({'error': str(e)}), 500

# Route to delete a file


@files_bp.route('/<int:fileId>', methods=['DELETE'])
def delete_file(fileId):
    try:
        user_id, user_gc_id = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        file_info = store.delete_file_entry(user_id, fileId)
        delete_from_gcs(user_gc_id, file_info['file_name'])
        return '', 204
    except Exception as e:
        logging.error(f"Error deleting file: {e}")
        return jsonify({'error': str(e)}), 500
