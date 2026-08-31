from firebase_admin import auth
import os
import asyncio
import re
from google.cloud import storage
import logging
from fastapi import UploadFile
from llama_index.core.node_parser import SentenceSplitter as RecursiveTextSplitter
from tiktoken import get_encoding
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import timedelta
from urllib.parse import urlparse, unquote, quote



logger = logging.getLogger(__name__)



bucket_name = 'docsynth-fbb02.appspot.com'

# Uploaded documents are private. The canonical URL below is the stable identity
# stored in files.file_url and baked into the citations of saved answers; it is
# deliberately NOT fetchable. Access goes through a short-lived signed URL minted
# per request, after the caller's workspace access has been checked.
GCS_PUBLIC_HOST = 'https://storage.googleapis.com'
# The service-account file, at the path it has inside the container. Overridable
# because it was hardcoded in three places, which meant nothing touching storage
# could run outside Docker: every upload, import and approval failed with
# FileNotFoundError before reaching Google. `firebase_setup.py` already solves
# the same problem the same way. The default is unchanged, so the container
# behaves exactly as before.
CREDENTIALS_PATH = os.getenv('GCS_CREDENTIALS_PATH', '/app/api/config/credentials.json')

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
    """Stable, unauthenticated-but-unreadable identity for a stored object.

    The path is percent-encoded. It was interpolated raw, so a document called
    "Employee Handbook 2026.pdf" produced a URL with literal spaces in it, and a
    citation to it is written into the answer as markdown. A space inside
    [text](url) ends the URL, so the link truncated at the first word and landed
    nowhere. Customer file names have spaces in them constantly; this was not an
    edge case, it was most of them.

    safe="/" keeps the path separators readable and encodes everything else,
    including the space, the hash and the question mark that would otherwise be
    read as a fragment or a query.
    """
    return f"{GCS_PUBLIC_HOST}/{bucket_name}/{quote(object_path, safe='/')}"


def object_path_from_url(file_url: str) -> Optional[str]:
    """Recover the bucket-relative object path from a stored file_url.

    One shape, the one canonical_gcs_url writes. Two older forms were accepted
    here for rows that might still carry them; no row does, checked before they
    were removed, so they were dead branches rather than live compatibility.

    Unquoted on the way back because the path is percent-encoded going out, and
    the object's real name in storage is the decoded one.
    """
    if not file_url:
        return None
    prefix = f"{GCS_PUBLIC_HOST}/{bucket_name}/"
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

def workspace_object_path(workspace_id: int, file_id: int, filename: str) -> str:
    """Where a document lives: under its workspace, keyed by its row.

    Storage used to be keyed by the uploader — {firebase_uid}/{filename} —
    while access, retrieval and citations were all keyed by workspace. That one
    mismatch is what made deleting a person delete documents belonging to a
    company they had merely joined, and what forced the worker to reverse the
    uploader's uid out of a URL just to fetch bytes it already had the id for.

    A document belongs to a workspace. Deleting a workspace is now deleting a
    prefix, and deleting a person touches no storage at all.

    The row id leads the filename because the workspace folder is shared. Keyed
    on name alone, two people uploading invoice.pdf into the same workspace
    would write the same object: the second overwrites the first's bytes, two
    rows point at one object, and deleting either takes the other's document
    with it. Under the old uploader-keyed layout that could only collide with
    yourself, which read as replacing your own file. It cannot collide now,
    because no two rows share an id.
    """
    return f"workspaces/{workspace_id}/{file_id}-{filename}"


async def upload_to_gcs(file: UploadFile, workspace_id: int, file_id: int, filename: str):
    try:
        logger.debug(f"Uploading file {filename} to GCS for workspace {workspace_id}...")

        # Initialize GCS client with explicit credentials file path
        logger.info(f"Using GCS credentials from {CREDENTIALS_PATH}")
        client = storage.Client.from_service_account_json(CREDENTIALS_PATH)
        bucket = client.bucket(bucket_name)
        object_path = workspace_object_path(workspace_id, file_id, filename)
        blob = bucket.blob(object_path)

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
        url = canonical_gcs_url(object_path)
        logger.info(f"Successfully uploaded {filename} to GCS with content type {content_type}: {url}")
        return url

    except Exception as e:
        logger.error(f"Error uploading {filename} to GCS: {e}")
        return None

async def upload_bytes_to_gcs(data: bytes, workspace_id: int, file_id: int, filename: str):
    """Store bytes we already hold, under the same layout an upload uses.

    Exists for connectors, which fetch a document from Drive or SharePoint and
    have it in memory rather than as an UploadFile. Deliberately the same object
    path, the same content type mapping and the same "never public" rule as
    upload_to_gcs, because an imported document must be indistinguishable from
    an uploaded one everywhere downstream.
    """
    try:
        client = storage.Client.from_service_account_json(CREDENTIALS_PATH)
        bucket = client.bucket(bucket_name)
        object_path = workspace_object_path(workspace_id, file_id, filename)
        blob = bucket.blob(object_path)

        extension = filename.lower().split('.')[-1] if '.' in filename else ''
        content_type = {
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'md': 'text/markdown',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }.get(extension, 'application/octet-stream')

        await asyncio.to_thread(blob.upload_from_string, data, content_type=content_type)
        blob.content_disposition = 'inline'
        await asyncio.to_thread(blob.patch)

        url = canonical_gcs_url(object_path)
        logger.info(f"Stored imported {filename} ({len(data)} bytes) at {url}")
        return url
    except Exception as e:
        logger.error(f"Error storing imported {filename}: {e}", exc_info=True)
        return None


def download_bytes(object_path: str):
    """Fetch an object by its path.

    Took (uploader_uid, filename) before, so anything wanting to read a
    document had to know who had uploaded it — which the worker does not, and
    reversed out of the URL to compensate. files.file_url already carries the
    full path, for objects under the old layout and the new one alike, which is
    why nothing had to be moved.
    """
    if not object_path:
        return None
    try:
        client = _gcs_client()
        blob = client.bucket(bucket_name).blob(object_path)
        if not blob.exists():
            logger.warning(f"Object {object_path} not found in GCS")
            return None
        data = blob.download_as_bytes()
        logger.info(f"Downloaded {object_path} from GCS")
        return data
    except Exception as e:
        logger.error(f"Error downloading {object_path} from GCS: {e}")
        return None


def download_from_gcs(file_url: str):
    """Fetch a document by its stored URL."""
    return download_bytes(object_path_from_url(file_url))

def delete_from_gcs(file_url: str):
    """Delete one document by its stored URL."""
    object_path = object_path_from_url(file_url)
    if not object_path:
        logger.warning(f"Cannot delete unrecognised file_url: {file_url}")
        return
    try:
        blob = _gcs_client().bucket(bucket_name).blob(object_path)
        if not blob.exists():
            logger.warning(f"Object {object_path} not found in GCS")
            return
        blob.delete()
        logger.info(f"Deleted {object_path} from GCS")
    except Exception as e:
        logger.error(f"Error deleting {object_path} from GCS: {e}")


def move_object_to_workspace(file_url: str, workspace_id: int) -> Optional[str]:
    """Relocate a stored object into another workspace's folder.

    Returns the new canonical URL, or None if it could not be moved.

    Without this the path and the row disagree the moment a document is moved
    between workspaces: the object stays under the old workspace's prefix while
    the row claims the new one. Deleting the old workspace would then destroy a
    document belonging to the new one, and deleting the new one would leave the
    object behind. The invariant this whole layout rests on is that a
    document's path names the workspace it is in.

    The leaf name comes from the object being moved, not from the row's
    file_name, so the id prefix that makes it unique travels with it. Rebuilding
    the name here would strip that prefix back off and hand the target workspace
    a collision.
    """
    source_path = object_path_from_url(file_url)
    if not source_path:
        logger.warning(f"Cannot move unrecognised file_url: {file_url}")
        return None

    target_path = f"workspaces/{workspace_id}/{source_path.rsplit('/', 1)[-1]}"
    if source_path == target_path:
        return file_url

    try:
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        source = bucket.blob(source_path)
        if not source.exists():
            logger.warning(f"Cannot move {source_path}: not found")
            return None

        # Copy then delete, rather than trusting a rename to be atomic. A failed
        # delete leaves a duplicate, which is recoverable; a failed copy after a
        # delete loses the document.
        bucket.copy_blob(source, bucket, target_path)
        source.delete()
        logger.info(f"Moved {source_path} to {target_path}")
        return canonical_gcs_url(target_path)
    except Exception as e:
        logger.error(f"Error moving {source_path} to {target_path}: {e}")
        return None


def delete_workspace_objects(workspace_id: int) -> int:
    """Delete everything stored for a workspace. Returns how many went.

    The whole point of mirroring workspaces in the bucket: removing a
    workspace's documents is removing a prefix, rather than working out which
    of somebody's uploads happened to live there.
    """
    prefix = f"workspaces/{workspace_id}/"
    deleted = 0
    try:
        client = _gcs_client()
        for blob in client.list_blobs(bucket_name, prefix=prefix):
            blob.delete()
            deleted += 1
        logger.info(f"Deleted {deleted} object(s) under {prefix}")
    except Exception as e:
        logger.error(f"Error deleting objects under {prefix}: {e}")
    return deleted


def detect_content_type(text: str) -> str:
    """Heuristically detect content type from text.

    There used to be a "csv_like" answer here, returned for any text with a
    comma and more than three lines. See clean_text for what it then did and
    why both halves are gone.
    """
    if re.search(r'Page \d+', text, re.IGNORECASE):
        return "pdf"
    if re.search(r'^#+\s|\n#{1,6}\s', text):
        return "markdown"
    return "text"

def clean_text(text: str, content_type: str) -> str:
    """Clean irrelevant elements or markers from text.

    WHAT WAS REMOVED HERE, AND WHY, 2026-08-15

    A third branch stripped every character except letters, digits, commas,
    dots, spaces and newlines, on the theory that it was cleaning up a CSV. It
    fired on any text holding a comma and four lines, which is ordinary business
    prose, and it never fired on a PDF at all: a PDF page carries its "Page N"
    marker and matched the branch above first. So the documents it damaged were
    exactly the DOCX and TXT files, and the benchmark corpus is PDFs, which is
    why nothing measured it.

    Run over a policy section, it produced:

        Fees are non-refundable (see Schedule A).  -> Fees are nonrefundable see Schedule A.
        Contact billing@acme.com or call 555-0134. -> Contact billingacme.com or call 5550134.
        1.5% interest, above $1,200/month          -> 1.5 interest, above 1,200month
        | Plan | Price |                           ->   Plan  Price

    That text is not only what a reader sees behind a citation. It is what gets
    embedded, what the keyword index is built from, and what the model is given
    to answer from. It also defeats the literal-token arm of the search, which
    exists to let a rare token like an error code or a part number decide the
    ranking: a customer searching 555-0134 cannot match 5550134.

    Nothing downstream wanted this. Real CSV files are not a supported upload
    type, and if they become one they need a parser, not a regex that deletes
    punctuation from everything else on the way past.
    """
    if content_type == "pdf":
        text = re.sub(r'Page \d+(?: of \d+)?\s*\n?', '', text, flags=re.IGNORECASE)
    elif content_type == "markdown":
        text = re.sub(r'#.*?\n', '', text)
    return text.strip()

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

# A heading, in either shape the extractor produces: "## Servicing", and a line
# that is nothing but bold text, which is how a running page header arrives.
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*]{1,60}\*\*\s*$)")


def _is_heading_only(text: str) -> bool:
    """Whether this piece is a label with nothing under it."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or len(text) > 120:
        return False
    return all(_HEADING_LINE.match(ln) for ln in lines)


def _is_junk(text: str) -> bool:
    """A piece that cannot answer anything, whatever it is retrieved for.

    Bare page furniture: "60", "428 04 170100", "iii". No question has these as
    its answer, and each one costs an embedding and occupies a retrieval slot.

    Deliberately not a length rule. "Resistance at 25C: 5 kOhm." is 24
    characters and is exactly the kind of specification this product exists to
    find. The test is whether anything here is a word.
    """
    stripped = re.sub(r"[^A-Za-z]", "", text)
    return len(stripped) < 3


def _attach_orphans(pieces: List[str]) -> List[str]:
    """Give a heading the content it heads, and drop what is only furniture.

    WHY

    Measured 2026-08-30 over 147 real pages: 121 of 624 chunks, 19%, were under
    40 characters, and the most common was "**SERVICING**" twenty-three times.
    Those are running page headers. Structural extraction correctly identifies
    them as headings, which is an improvement in fidelity and made this worse,
    because a heading with no body still flushed as its own prose block.

    They are not junk in principle: "TROUBLESHOOTING" is real structure, and a
    passage is easier to place when it carries the heading it sits under. So
    they are attached forward rather than dropped, which is the same thing
    _chunk_table already does by repeating the caption into every piece.

    A trailing heading attaches backward, because there is nothing after it. A
    page consisting only of headings keeps them: dropping the page entirely is
    worse than a thin chunk.

    Bare page numbers are different and are dropped outright. A heading tells a
    reader where they are; "60" tells them nothing, and attaching it forward
    only puts a stray number at the head of an unrelated passage.
    """
    kept = [p for p in pieces if not _is_junk(p)]

    out: List[str] = []
    pending: List[str] = []
    for piece in kept:
        if _is_heading_only(piece):
            pending.append(piece)
            continue
        if pending:
            piece = "\n".join(pending + [piece])
            pending = []
        out.append(piece)
    if pending:
        if out:
            out[-1] = out[-1] + "\n" + "\n".join(pending)
        else:
            out.extend(pending)
    return out


# A section heading. Only the "#" form: measured on real extractor output, a
# bold-only line is as often half a sentence ("**BEGINNING REPAIRS.**") as it is
# a heading, so it stays content and is handled by _attach_orphans instead.
_MD_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")


def _split_markdown_blocks(text: str) -> List[Dict[str, Any]]:
    """Separate a page into table blocks and everything else, under its headings.

    A markdown table is a run of consecutive lines that all start and end with
    a pipe. The heading line immediately above it, if there is one, belongs to
    the table: a chunk reading "| 251 | 78 | 76 | 74 |" is useless without
    something saying it is the required liquid line temperature.

    Each block also carries the heading path it sits under, so a passage about
    defrost intervals knows it is under TROUBLESHOOTING. Headings are consumed
    into that path rather than left in the text: they come back as a prefix on
    every piece cut from the block, which is what stops one section's heading
    from being the only thing a chunk contains.
    """
    lines = text.splitlines()
    blocks: List[Dict[str, Any]] = []
    buf: List[str] = []
    stack: List[Tuple[int, str]] = []

    def path() -> List[str]:
        return [title for _, title in stack]

    def flush_prose():
        if buf:
            blocks.append({"kind": "prose", "lines": list(buf), "path": path()})
            buf.clear()

    i = 0
    while i < len(lines):
        heading = _MD_HEADING.match(lines[i])
        if heading:
            flush_prose()
            level = len(heading.group(1))
            title = heading.group(2).replace("*", "").strip()
            # A deeper heading nests; an equal or shallower one replaces.
            while stack and stack[-1][0] >= level:
                stack.pop()
            if title:
                stack.append((level, title))
            i += 1
            continue

        if _TABLE_ROW.match(lines[i]):
            j = i
            while j < len(lines) and (_TABLE_ROW.match(lines[j]) or not lines[j].strip()):
                # A blank line inside a table ends it only if nothing pipe-shaped
                # follows, so a stray blank between header and body is survivable.
                if not lines[j].strip():
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k >= len(lines) or not _TABLE_ROW.match(lines[k]):
                        break
                j += 1

            # The caption or heading directly above the table comes with it.
            caption: List[str] = []
            while buf and not buf[-1].strip():
                buf.pop()
            if buf and not _TABLE_ROW.match(buf[-1]):
                caption = [buf.pop()]
            flush_prose()
            blocks.append({
                "kind": "table",
                "caption": caption,
                "lines": [ln for ln in lines[i:j] if ln.strip()],
                "path": path(),
            })
            i = j
            continue
        buf.append(lines[i])
        i += 1

    flush_prose()
    return blocks


def _chunk_table(block: Dict[str, Any], count_tokens, target_chunk_tokens: int) -> List[str]:
    """Cut a table on row boundaries, repeating its header in every piece.

    WHY THIS EXISTS

    Measured 2026-08-13 on a reconstructed Carrier charging chart. The general
    splitter turned a 1,725-character table into three chunks: the second began
    "| 80 | 78 | 76 | 74 | 72 | 70 |" with the row's own pressure value left
    behind in the first chunk, and neither the second nor the third carried the
    header row. A chunk of numbers with no row key and no column names cannot
    answer anything.

    That is the same failure the vision model is bought to fix, one stage later,
    and it explains a baseline result that otherwise made no sense: benchmark
    question 6 cited the correct page and still answered 78 instead of 74. No
    extraction quality could have saved it, because the retrieved chunk could
    not have held an intact row.

    So: never cut inside a row, and every piece repeats the caption, the header
    and the separator. A single row wider than the budget is emitted alone and
    over-length rather than cut, because half a row is worse than a long one.
    """
    # The section path joins the caption, deduplicated. The caption is the line
    # directly above the table and is usually the table's own title, which is
    # more specific than the section; the path says which section that title is
    # in. Both, because "DELUXE SPLIT CONDENSERS" and "PRODUCT IDENTIFICATION"
    # answer different halves of "what am I looking at".
    caption = list(block.get("path") or [])
    for line in block.get("caption") or []:
        if line.strip() and line.strip() not in caption:
            caption.append(line)
    rows = block["lines"]
    if not rows:
        return []

    # Header is the first row plus the |---|---| separator when present.
    header = rows[:1]
    body_start = 1
    if len(rows) > 1 and set(rows[1].replace("|", "").strip()) <= set("-: "):
        header = rows[:2]
        body_start = 2
    body = rows[body_start:]
    if not body:
        return ["\n".join(caption + rows)]

    prefix = caption + header
    prefix_tokens = count_tokens("\n".join(prefix))

    out: List[str] = []
    current: List[str] = []
    current_tokens = prefix_tokens
    for row in body:
        row_tokens = count_tokens(row)
        if current and current_tokens + row_tokens > target_chunk_tokens:
            out.append("\n".join(prefix + current))
            current = []
            current_tokens = prefix_tokens
        current.append(row)
        current_tokens += row_tokens
    if current:
        out.append("\n".join(prefix + current))
    return out


def chunk_markdown(text: str, target_chunk_tokens: int = 400) -> List[Dict[str, Any]]:
    """Chunk a page that came from the vision model, respecting its tables.

    Prose in the page still goes through the ordinary splitter, because it is
    ordinary prose and that splitter handles it well; the prose questions in the
    HVAC benchmark score 4/4 on citations today and must not move.
    """
    from tiktoken import get_encoding

    enc = get_encoding("cl100k_base")

    def count_tokens(content: str) -> int:
        return len(enc.encode(content))

    blocks = _split_markdown_blocks(text)

    # (section path, piece). The path travels with the piece so orphan
    # attachment and junk removal still see the bare text, and the breadcrumb is
    # added last. Prefixing first would make every piece look substantial and
    # nothing would ever be recognised as furniture.
    pieces: List[Tuple[List[str], str]] = []
    for block in blocks:
        path = block.get("path") or []
        if block["kind"] == "table":
            for piece in _chunk_table(block, count_tokens, target_chunk_tokens):
                pieces.append((path, piece))
            continue
        prose = "\n".join(block["lines"]).strip()
        if not prose:
            continue
        if count_tokens(prose) <= target_chunk_tokens:
            pieces.append((path, prose))
            continue
        splitter = RecursiveTextSplitter(
            chunk_size=target_chunk_tokens,
            chunk_overlap=int(target_chunk_tokens * 0.2),
        )
        for piece in splitter.split_text(prose):
            pieces.append((path, piece))

    # Orphan attachment runs within a section, not across one. A heading at the
    # end of a section belongs to that section, and merging it into the next
    # one would file it under the wrong place.
    resolved: List[Tuple[List[str], str]] = []
    run: List[str] = []
    run_path: Optional[List[str]] = None
    # Headings that had nothing to attach to in their own section. A run that
    # is nothing but headings is page furniture sitting above the next real
    # section, so it crosses the boundary rather than becoming its own chunk.
    # Keeping the rule strictly within-section put "**SERVICING**" back on its
    # own, which is the thing this exists to prevent.
    carry: List[str] = []

    def close_run():
        nonlocal carry
        if not run:
            return
        attached = _attach_orphans(carry + run)
        if all(_is_heading_only(p) for p in attached):
            carry = attached
            return
        carry = []
        resolved.extend((run_path, p) for p in attached)

    for path, piece in pieces + [(None, None)]:
        if path != run_path:
            close_run()
            run, run_path = [], path
        if piece is not None:
            run.append(piece)
    close_run()
    if carry and resolved:
        # Nothing ever followed them. Attach backward rather than drop.
        last_path, last = resolved[-1]
        resolved[-1] = (last_path, last + "\n" + "\n".join(carry))

    out: List[Dict[str, Any]] = []
    for i, (path, piece) in enumerate(resolved):
        if not piece.strip():
            continue
        # The breadcrumb goes in the text, not only in metadata, because the
        # text is what gets embedded and what the keyword arm searches. This is
        # the same reason _chunk_table repeats its caption into every piece: a
        # passage that does not say what it is about can only be found by the
        # words that happen to be in it.
        crumb = " > ".join(path) if path else ""
        content = f"{crumb}\n{piece}" if crumb and crumb not in piece else piece
        out.append({
            "content": content,
            "metadata": {
                "section": i + 1,
                "doc_type": "markdown",
                # The real section, as opposed to the counter above, which is
                # just this piece's position on the page.
                "section_path": path,
            },
        })
    return out


def chunk_text(
    text: str,
    content_type: str = None,
    max_chunks_per_section: int = 5,
    target_chunk_tokens: int = 400
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

        # chunk_size is in tokens. It used to be multiplied by four for PDFs,
        # so the splitter fired at 800 tokens against pages averaging about 520
        # and almost never fired: a chunk became a page, which nobody chose and
        # which every measurement about chunking was then really about.
        #
        # 400 with 20% overlap, the same for every format. A page becomes two or
        # three retrieval units instead of one, and the page is still what gets
        # cited because that is what a reader opens.
        splitter = RecursiveTextSplitter(
            chunk_size=target_chunk_tokens,
            chunk_overlap=int(target_chunk_tokens * 0.2),
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
