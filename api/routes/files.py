import os
import logging
from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id
from doc_processor import process_file
from google.cloud import storage
from redis.exceptions import RedisError
from celery_worker import celery_app  # Adjust this import
from postgresql_store import DocSynthStore
import redis 
from context_processor import context,generate_interpretation
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

bucket_name = 'docsynth-fbb02.appspot.com'
files_bp = Blueprint("files", __name__, url_prefix="/api/v1/files")
db_name = os.getenv("DATABASE_NAME")
db_user = os.getenv("DATABASE_USER")
db_pwd = os.getenv("DATABASE_PASSWORD")
db_host = os.getenv("DATABASE_HOST")
db_port = os.getenv("DATABASE_PORT")

database_config = {
        'dbname': db_name,
        'user': db_user,
        'password': db_pwd,
        'host': db_host,
        'port': db_port
    }

celery_store = DocSynthStore(database_config) 
def get_id_helper(success, user_info):
    if not success:
        return jsonify(user_info), 401
    return user_info['user_id']

def delete_from_gcs(user_id, file_name):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        user_folder = f"{user_id}/"
        blob = bucket.blob(user_folder + file_name)
        blob.delete()
        logging.info(f"Deleted {file_name} from GCS.")
    except Exception as e:
        logging.error(f"Error deleting from GCS: {e}")

def upload_to_gcs(file_data, user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        user_folder = f"{user_gc_id}/"
        blob = bucket.blob(user_folder + filename)
        
        blob.upload_from_file(file_data, content_type=file_data.mimetype)
        blob.make_public()
        file_url = blob.public_url
        logging.info(f"Uploaded {filename} to GCS: {file_url}")
        return file_url
    except Exception as e:
        logging.error(f"Error uploading to GCS: {e}")
        return None

def download_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        user_folder = f"{user_gc_id}/"
        blob = bucket.blob(user_folder + filename)
        file_data = blob.download_as_bytes()
        logging.info(f"Downloaded {filename} from GCS.")
        return file_data
    except Exception as e:
        logging.error(f"Error downloading from GCS: {e}")
        return None

@celery_app.task
def process_chunk(chunk, topic, sources_list, belief_system):
    return generate_interpretation(chunk, topic, sources_list, belief_system)

@celery_app.task
def combine_results(results):
    return "\n\n".join(results)

@celery_app.task
def process_and_store_file(user_id, user_gc_id, filename):
    try:
        logging.info(f"Started processing file: {filename}")
        # Download file from GCS
        file_data = download_from_gcs(user_gc_id, filename)
        if file_data is None:
            raise FileNotFoundError(f"File {filename} not found in GCS for user {user_gc_id}")

        _, file_extension = os.path.splitext(filename)
        file_extension = file_extension.lower().strip('.')
        # Process the file
        doc_info = process_file(file_data, file_extension)
        if len(doc_info.strip()) > 0:
            chunks, topic, sources_list, belief_system = context(doc_info)

            chunk_results = []
            chunk_results = []
            for i, chunk in enumerate(chunks):
                # Spread out tasks by 2 seconds between each task
                async_result = process_chunk.apply_async(
                    (chunk, topic, sources_list, belief_system),
                    countdown=1  # Adds a delay of 1s between each task submission
                )
                chunk_results.append(async_result)

                # Delay before processing the next chunk (adjust delay as needed)
               
            # Combine results
            response = combine_results.apply_async((chunk_results,)).get()  # Get the final response

            # Format the message
            message = f"""
            The document **{filename}** has been successfully processed.

            **Interpretation:**
            {response}
            """ 
            celery_store.add_message(content=message, sender='bot', user_id=user_id)

    except Exception as e:
        logging.error(f"Error processing file {filename}: {e}")


@files_bp.route('', methods=['GET'])
def retrieve_files():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = store.get_user_id_from_email(user_info['email'])
        files = store.get_files_for_user(user_id)
        return jsonify(files)
    except Exception as e:
        logging.error(str(e))
        return jsonify({'error': str(e)}), 500

@files_bp.route('', methods=['POST'])
def save_file():
    try:
        print('Starting file upload process')
        store = current_app.store
      
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = store.get_user_id_from_email(user_info['email'])
     

        if not request.files:
            logging.info('No files provided')
            return jsonify({'error': 'No files provided'}), 400

        for file_key, file in request.files.items():
            logging.info(f"Received file: {file.filename}")

            # Upload the file to GCS
            file_url = upload_to_gcs(file, user_info['user_id'], file.filename)
            if file_url is None:
                return jsonify({'error': 'File upload failed'}), 500

            # Enqueue the file processing task
            try:
                file_info = store.add_file(user_id, file.filename, file_url)
                logging.info(f"Stored file metadata: {file.filename}")
                process_and_store_file(user_id, user_info['user_id'], file.filename)
                logging.info(f"Enqueued processing for file: {file.filename}")
            except RedisError as e:
                logging.error(f"Error enqueueing job: {e}")
                return jsonify({'error': 'Failed to enqueue job'}), 500

        return jsonify({'message': 'File processing Queued.'})

    except Exception as e:
        logging.error(f"Exception occurred: {e}")
        return jsonify({'error': str(e)}), 500

@files_bp.route('/<int:fileId>', methods=['DELETE'])
def delete_file(fileId):
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        user_id = store.get_user_id_from_email(user_info['email'])
        file_dict = store.delete_file_entry(user_id, fileId)
        delete_from_gcs(user_info['user_id'], file_dict['file_name'])
        return '', 204
    except Exception as e:
        logging.error(str(e))
        return jsonify({'error': str(e)}), 500
