from firebase_admin import auth
import base64
import re
import hashlib
from google.cloud import storage
import logging
from fastapi import UploadFile
import json
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


async def upload_to_gcs(file: UploadFile, user_gc_id: str, filename: str):
    try:
        logger.debug(f"Uploading file {filename} to GCS for user {user_gc_id}...")

        # Initialize GCS client with explicit credentials file path
        credentials_path = '/app/api/config/credentials.json'
        logger.info(f"Using GCS credentials from {credentials_path}")
        client = storage.Client.from_service_account_json(credentials_path)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")

        # Determine the correct content type based on file extension
        content_type = "application/octet-stream"  # Default type
        extension = filename.lower().split('.')[-1] if '.' in filename else ''
        
        # Map common extensions to MIME types
        mime_types = {
            'pdf': 'application/pdf',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'txt': 'text/plain',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'ppt': 'application/vnd.ms-powerpoint',
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'csv': 'text/csv',
            'mp4': 'video/mp4',
            'mov': 'video/quicktime',
            'mp3': 'audio/mpeg',
        }
        
        # Set content type if we recognize the extension
        if extension in mime_types:
            content_type = mime_types[extension]
            logger.info(f"Setting content type to {content_type} for file {filename}")

        # Stream file upload using chunks
        with blob.open("wb") as gcs_file:
            while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
                gcs_file.write(chunk)
                
        # Set the content type and other metadata
        blob.content_type = content_type
        blob.metadata = {'Content-Disposition': 'inline'}
        blob.patch()

        # Optionally make the file public
        blob.make_public()

        public_url = blob.public_url
        logger.info(f"Successfully uploaded {filename} to GCS with content type {content_type}: {public_url}")
        return public_url  

    except Exception as e:
        logger.error(f"Error uploading {filename} to GCS: {e}")
        return None

def download_from_gcs(user_gc_id, filename):
    try:
        logger.debug(f"Downloading file {filename} from GCS for user {user_gc_id}...")
        # Use explicit credentials file path instead of default credentials
        credentials_path = "/app/api/config/credentials.json"
        client = storage.Client.from_service_account_json(credentials_path)
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
        # Use explicit credentials file path instead of default credentials
        credentials_path = "/app/api/config/credentials.json"
        client = storage.Client.from_service_account_json(credentials_path)
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

    """
    Parse JSON string from LLM, handling single quotes and other common issues.
    
    Args:
        json_string: Raw JSON string from LLM (e.g., DSPy output)
    
    Returns:
        List of parsed JSON objects, or empty list if parsing fails
    """
    if not json_string:
        logger.warning("Empty JSON string provided for parsing")
        return []
    
    try:
        # Strip ```json and ``` markers if present
        json_string = json_string.strip()
        if json_string.startswith("```json"):
            json_string = json_string[7:].rstrip("```").strip()
        
        # Replace single quotes with double quotes for JSON properties
        def replace_quotes(match):
            return f'"{match.group(1)}":'
        json_string = re.sub(r"'(\w+)'\s*:", replace_quotes, json_string)
        
        # Remove trailing commas before closing brackets
        json_string = re.sub(r',(\s*[}\]])', r'\1', json_string)
        
        # Parse JSON
        parsed = json.loads(json_string)
        if not isinstance(parsed, list):
            logger.warning(f"Parsed JSON is not a list: {type(parsed)}")
            return []
        return parsed
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON: {str(e)}")
        logger.debug(f"Problematic JSON (first 500 chars): {json_string[:500]}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error parsing JSON: {str(e)}")
        return []