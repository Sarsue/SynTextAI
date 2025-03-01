from firebase_admin import auth
import base64
import re
import hashlib
from google.cloud import storage
import logging
from flask_socketio import emit

bucket_name = 'docsynth-fbb02.appspot.com'

def decode_firebase_token(token):
    try:
        # Verify the token
        decoded_token = auth.verify_id_token(token)
        # Access user information from decoded token
        display_name = decoded_token.get('name', None)
        email = decoded_token.get('email', None)
        user_id = decoded_token.get('user_id', None)
        return True, {'name': display_name, 'email': email, 'user_id': user_id}
    except auth.ExpiredIdTokenError:
        return False, {'error': 'Token has expired'}
    except auth.InvalidIdTokenError as e:
        print(f'Invalid Token Error: {e}')
        return False, {'error': 'Invalid token'}
    except Exception as e:
        return False, {'error': str(e)}


def get_user_id(token):
    token = token.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    return success, user_info
   

def format_timestamp(seconds: float) -> str:
    """ Converts seconds to SRT timestamp format (hh:mm:ss, SSS)."""
    mins, secs = divmod (seconds, 60)
    hrs, mins = divmod(mins,60)
    ms = int((secs - int(secs)) * 1000)
    return f"{int(hrs):02}:{int(mins):02}:{int(secs):02}:{int(ms):03}"

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
        if not blob.exists():
            logging.warning(f"File {filename} not found in GCS")
            return None
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


def chunk_text(text):
    chunks = []
    paragraphs = text.split("\n")
        
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if paragraph:
                # Create a chunk with content and page metadata
            chunk = {
                    "content": paragraph,
                  #  "page_number": page_num
            }
        chunks.append(chunk)
    return chunks

   
def notify_user(socketio, user_id, event_type, data):
    """Send real-time notification to a specific user"""
    try:
        socketio.emit(event_type, data, room=str(user_id))
        logger.info(f"Notified user {user_id} with event {event_type}")
    except Exception as e:
        logger.error(f"Error notifying user {user_id}: {e}")