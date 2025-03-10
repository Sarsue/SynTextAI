from firebase_admin import auth
import base64
import re
import hashlib
from google.cloud import storage
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bucket_name = 'docsynth-fbb02.appspot.com'

def decode_firebase_token(token):
    try:
        logger.debug("Decoding Firebase token...")
        # Verify the token
        decoded_token = auth.verify_id_token(token)
        # Access user information from decoded token
        display_name = decoded_token.get('name', None)
        email = decoded_token.get('email', None)
        user_id = decoded_token.get('user_id', None)
        logger.info(f"Successfully decoded token for user: {email}")
        return True, {'name': display_name, 'email': email, 'user_id': user_id}
    except auth.ExpiredIdTokenError:
        logger.error("Token has expired")
        return False, {'error': 'Token has expired'}
    except auth.InvalidIdTokenError as e:
        logger.error(f"Invalid Token Error: {e}")
        return False, {'error': 'Invalid token'}
    except Exception as e:
        logger.error(f"Unexpected error decoding token: {e}")
        return False, {'error': str(e)}

def get_user_id(token):
    try:
        logger.debug("Extracting user ID from token...")
        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)
        if success:
            logger.info(f"Successfully extracted user ID: {user_info['user_id']}")
        else:
            logger.error(f"Failed to extract user ID: {user_info['error']}")
        return success, user_info
    except Exception as e:
        logger.error(f"Error extracting user ID: {e}")
        return False, {'error': str(e)}

def format_timestamp(seconds: float) -> str:
    """Converts seconds to SRT timestamp format (hh:mm:ss, SSS)."""
    try:
        logger.debug(f"Formatting timestamp for {seconds} seconds...")
        mins, secs = divmod(seconds, 60)
        hrs, mins = divmod(mins, 60)
        ms = int((secs - int(secs)) * 1000)
        formatted_time = f"{int(hrs):02}:{int(mins):02}:{int(secs):02}:{int(ms):03}"
        logger.info(f"Formatted timestamp: {formatted_time}")
        return formatted_time
    except Exception as e:
        logger.error(f"Error formatting timestamp: {e}")
        raise


async def upload_to_gcs(file: UploadFile, user_gc_id: str, filename: str, bucket_name: str):
    try:
        logger.debug(f"Uploading file {filename} to GCS for user {user_gc_id}...")

        # Initialize GCS client
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")

        # Stream file upload using chunks
        with blob.open("wb") as gcs_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                gcs_file.write(chunk)

        # Optionally make the file public
        # blob.make_public()  

        public_url = blob.public_url
        logger.info(f"Successfully uploaded {filename} to GCS: {public_url}")
        return public_url  

    except Exception as e:
        logger.error(f"Error uploading {filename} to GCS: {e}")
        return None
        
def download_from_gcs(user_gc_id, filename):
    try:
        logger.debug(f"Downloading file {filename} from GCS for user {user_gc_id}...")
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        if not blob.exists():
            logger.warning(f"File {filename} not found in GCS")
            return None
        file_data = blob.download_as_bytes()
        logger.info(f"Successfully downloaded {filename} from GCS")
        return file_data
    except Exception as e:
        logger.error(f"Error downloading {filename} from GCS: {e}")
        return None

def delete_from_gcs(user_gc_id, filename):
    try:
        logger.debug(f"Deleting file {filename} from GCS for user {user_gc_id}...")
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        if not blob.exists():
            logger.warning(f"File {filename} not found in GCS")
            return
        blob.delete()
        logger.info(f"Successfully deleted {filename} from GCS")
    except Exception as e:
        logger.error(f"Error deleting {filename} from GCS: {e}")

def chunk_text(text):
    try:
        logger.debug("Chunking text...")
        chunks = []
        paragraphs = text.split("\n")
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if paragraph:
                chunk = {
                    "content": paragraph,
                }
                chunks.append(chunk)
        logger.info(f"Successfully chunked text into {len(chunks)} parts")
        return chunks
    except Exception as e:
        logger.error(f"Error chunking text: {e}")
        raise