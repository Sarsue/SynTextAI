from firebase_admin import auth
import re
from google.cloud import storage
import logging
from fastapi import UploadFile
from llama_index.core.node_parser import SentenceSplitter as RecursiveTextSplitter
from tiktoken import get_encoding
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import timedelta
from urllib.parse import urlparse, unquote



logger = logging.getLogger(__name__)



bucket_name = 'docsynth-fbb02.appspot.com'

# Uploaded documents are private. The canonical URL below is the stable identity
# stored in files.file_url and baked into the citations of saved answers; it is
# deliberately NOT fetchable. Access goes through a short-lived signed URL minted
# per request, after the caller's workspace access has been checked.
GCS_PUBLIC_HOST = 'https://storage.googleapis.com'
CREDENTIALS_PATH = '/app/api/config/credentials.json'

# Long enough to read a document, short enough that a leaked link is a
# non-event. Refreshed every time the viewer opens a file.
SIGNED_URL_TTL = timedelta(minutes=30)


def _gcs_client():
    return storage.Client.from_service_account_json(CREDENTIALS_PATH)


def sanitize_extracted_text(text: Optional[str]) -> str:
    """Strip bytes Postgres will not store from extracted document text.

    PDF text extraction happily returns NUL bytes, which a Postgres text column
    rejects outright: "invalid byte sequence for encoding UTF8: 0x00". The
    insert fails, the whole ingestion transaction rolls back, and a document
    that extracted perfectly well is marked failed with nothing in the UI to say
    why.

    Other C0 control characters are dropped for the same reason they are
    useless here: they are not content, they survive into chunks, and they end
    up in the text an answer is grounded in. Tab, newline and carriage return
    are kept because they carry layout.
    """
    if not text:
        return ""
    return "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or ord(ch) >= 32
    )


def canonical_gcs_url(object_path: str) -> str:
    """Stable, unauthenticated-but-unreadable identity for a stored object."""
    return f"{GCS_PUBLIC_HOST}/{bucket_name}/{object_path}"


def object_path_from_url(file_url: str) -> Optional[str]:
    """Recover the bucket-relative object path from a stored file_url.

    Tolerates the historical shapes: the storage.googleapis.com form written
    today and the appspot.com virtual-host form some older rows carry.
    """
    if not file_url:
        return None
    for prefix in (
        f"{GCS_PUBLIC_HOST}/{bucket_name}/",
        f"https://{bucket_name}.storage.googleapis.com/",
        f"https://storage.cloud.google.com/{bucket_name}/",
    ):
        if file_url.startswith(prefix):
            return unquote(urlparse(file_url[len(prefix):]).path)
    return None


def generate_signed_url(file_url: str, ttl: timedelta = SIGNED_URL_TTL) -> Optional[str]:
    """Mint a short-lived read URL for a stored object.

    Returns None rather than raising: a file whose URL cannot be signed should
    surface as "can't open this" in the viewer, not a 500 on the whole request.
    """
    object_path = object_path_from_url(file_url)
    if not object_path:
        logger.warning("Cannot sign unrecognised file_url: %s", file_url)
        return None
    try:
        blob = _gcs_client().bucket(bucket_name).blob(object_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=ttl,
            method="GET",
            # Without this the browser downloads instead of rendering, which
            # breaks the #page=N citation anchor.
            response_disposition="inline",
        )
    except Exception as e:
        logger.error(f"Error signing URL for {object_path}: {e}")
        return None

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
                
        # Set the content type and other metadata.
        #
        # This used to be blob.metadata = {'Content-Disposition': 'inline'},
        # which writes the *custom metadata* key x-goog-meta-Content-Disposition
        # and has no effect on how a browser treats the response. The real
        # header is content_disposition.
        blob.content_type = content_type
        blob.content_disposition = 'inline'
        blob.patch()

        # Deliberately NOT public.
        #
        # blob.make_public() used to run here, so every uploaded document was
        # world-readable to anyone holding the URL — no session, no credentials.
        # The URL leaks through copied citations, browser history, referrer
        # headers and forwarded answers, and the customers this is sold to
        # upload patient records, privileged files and tax documents. Reads now
        # go through generate_signed_url() after an access check.
        object_path = f"{user_gc_id}/{filename}"
        url = canonical_gcs_url(object_path)
        logger.info(f"Successfully uploaded {filename} to GCS with content type {content_type}: {url}")
        return url

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


def detect_content_type(text: str) -> str:
    """Heuristically detect content type from text."""
    if re.search(r'Page \d+', text, re.IGNORECASE):
        return "pdf"
    if re.search(r'^#+\s|\n#{1,6}\s', text):
        return "markdown"
    if "," in text and "\n" in text and len(text.splitlines()) > 3:
        return "csv_like"
    return "text"

def clean_text(text: str, content_type: str) -> str:
    """Clean irrelevant elements or markers from text."""
    if content_type == "pdf":
        text = re.sub(r'Page \d+(?: of \d+)?\s*\n?', '', text, flags=re.IGNORECASE)
    elif content_type == "markdown":
        text = re.sub(r'#.*?\n', '', text)
    elif content_type == "csv_like":
        # Keep commas, dots, newline, alphanumeric
        text = re.sub(r'[^\w,.\n ]+', '', text)
    return text.strip()

def chunk_text(
    text: str,
    content_type: str = None,
    max_chunks_per_section: int = 5,
    target_chunk_tokens: int = 200
) -> List[Dict[str, Any]]:
    """
    Universal text chunker using LlamaIndex RecursiveTextSplitter for semantic splitting.
    Produces overlapping chunks for RAG or QA.
    """
    try:
        if not text.strip():
            return []

        content_type = content_type or detect_content_type(text)
        text = clean_text(text, content_type)
        logger.debug(f"Chunking {content_type} text of length {len(text)}")

        # Use tiktoken for accurate token counting
        
        tiktoken_enc = get_encoding("cl100k_base")  # OpenAI's tokenizer, good approximation

        def count_tokens(content: str) -> int:
            return len(tiktoken_enc.encode(content))

        # Configure splitter
        separators = ["\n\n", "\n", ".", " ", ""]  # Recursive splitting
        splitter = RecursiveTextSplitter(
            chunk_size=target_chunk_tokens * 4 if content_type == "pdf" else target_chunk_tokens,
            chunk_overlap=int(target_chunk_tokens * 0.2)
        )

        split_chunks = splitter.split_text(text)
        chunks = [
            {"content": chunk, "metadata": {"section": i + 1, "doc_type": content_type}}
            for i, chunk in enumerate(split_chunks)
        ]

        logger.info(f"Chunked {content_type} text into {len(chunks)} parts")
        return chunks

    except Exception as e:
        logger.error(f"Error chunking text: {e}", exc_info=True)
        return []
