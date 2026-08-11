"""
Text processor module - Handles extraction and processing of plain text
and Markdown files. Mirrors PDFProcessor/DocxProcessor's shape so chunks
flow through the same downstream storage/retrieval path.

Replaces an earlier version of this file that predated the current
architecture: it imported from module paths that no longer exist
(`from processors...` instead of `from api.processors...`), called
`extract_image_text` which was never defined anywhere in this codebase,
and called repository methods (`store.add_chunk`, `store.add_key_concept`)
that don't exist on the current RepositoryManager. It also resurrected
key-concept generation, which was deliberately removed as part of the
EdTech feature cleanup. Never wired into the factory, never exercised.
"""
import logging
import gc
import re
from typing import Dict, List, Any

from api.repositories.repository_manager import RepositoryManager
from api.processors.base_processor import FileProcessor
from api.services.llm_service import get_text_embeddings_in_batches
from api.services.contextualizer import add_context, embedding_text
from api.core.utils import chunk_text

logger = logging.getLogger(__name__)

HEADING_RE = re.compile(r"^#{1,6}\s+(.*)")


class TextProcessor(FileProcessor):
    """
    Processor for plain text (.txt) and Markdown (.md) documents.
    Handles text extraction and embedding generation.
    """

    def __init__(self, store: RepositoryManager):
        """
        Initialize the text processor.

        Args:
            store: RepositoryManager instance for database operations
        """
        super().__init__()
        self.store = store

    async def process(self,
                     file_data: bytes,
                     file_id: int,
                     user_id: int,
                     filename: str,
                     **kwargs) -> Dict[str, Any]:
        """
        Process a text/Markdown file: extract text, generate embeddings.

        Args:
            file_data: Raw file data in bytes
            file_id: Database ID of the file
            user_id: ID of the user who owns the file
            filename: Name of the file
            **kwargs: Additional arguments

        Returns:
            Dictionary containing processing results
        """
        logger.info(f"Processing text file: {filename} (ID: {file_id}, User: {user_id})")

        section_data = self.extract_text_with_sections(file_data)
        logger.info(f"Text extraction complete. Sections: {len(section_data)}")

        if not section_data:
            logger.error(f"Failed to extract content from text file: {filename}")
            return {
                "success": False,
                "file_id": file_id,
                "error": "Failed to extract content from file",
                "metadata": {
                    "processor_type": "text"
                }
            }

        logger.info(f"Starting section processing and embedding generation for file {filename}")
        try:
            await self.store.file_repo.update_file_status(int(file_id), "embedding")
        except Exception:
            logger.debug("Non-fatal: could not update status to 'embedding'")
        # Chunks are written batch by batch inside this call, so a crash keeps
        # the sections it already reached and the answer can already cite them.
        result = await self.embed_and_store_pages(
            section_data,
            file_id=int(file_id),
            user_id=user_id,
            filename=filename,
            file_type="text",
        )

        # A document that extracted nothing is a failure, not a success. A
        # processor could catch every section, produce zero chunks, and still
        # mark the file processed, leaving a document that looks ready in the
        # list and answers nothing. Counted across the whole document, not this
        # attempt: a resumed run that finds everything already stored did no
        # work and is finished, not empty.
        if result["stored_chunks"] == 0 and result["skipped_pages"] == 0:
            logger.error(
                f"No chunks extracted from {filename}; marking it failed rather "
                f"than leaving a document that looks ready and answers nothing"
            )
            try:
                await self.store.file_repo.update_file_status(int(file_id), "failed")
            except Exception:
                logger.debug("Non-fatal: could not update status to 'failed'")
            return {
                "success": False,
                "file_id": file_id,
                "error": "No content could be extracted from this document",
                "metadata": {"processor_type": "text"},
            }

        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "section_count": len(section_data),
                "chunk_count": result["stored_chunks"],
                "resumed_pages": result["skipped_pages"],
                "processor_type": "text"
            }
        }

    def extract_text_with_sections(self, file_data: bytes) -> List[Dict[str, Any]]:
        """
        Decode the file and split into logical sections at Markdown headings
        (# through ######). Plain .txt files have no heading syntax, so they
        naturally fall through to a single section covering the whole file.
        Same "section stands in for a page number" approach as DocxProcessor,
        for the same reason: something to anchor a citation to.

        Args:
            file_data: Raw file data in bytes

        Returns:
            List of dictionaries with section numbers and text content
        """
        try:
            text = file_data.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = file_data.decode('latin-1')
                logger.warning("File was not valid UTF-8, decoded as latin-1")
            except Exception as e:
                logger.error(f"Could not decode file as text: {e}", exc_info=True)
                return []
        except Exception as e:
            logger.error(f"Error reading text file: {e}", exc_info=True)
            return []

        sections: List[Dict[str, Any]] = []
        current_heading = None
        current_lines: List[str] = []

        def flush_section():
            content = "\n".join(current_lines).strip()
            if not content and current_heading:
                content = current_heading
            if content:
                section_num = len(sections) + 1
                label = f"Section {section_num}"
                if current_heading:
                    label += f": {current_heading}"
                sections.append({
                    "page_num": section_num,
                    "text": f"{label}\n{content}"
                })

        for line in text.splitlines():
            match = HEADING_RE.match(line.strip())
            if match:
                flush_section()
                current_heading = match.group(1).strip()
                current_lines = []
            else:
                current_lines.append(line)
        flush_section()

        if not sections:
            logger.warning("Text file had no extractable content")

        return sections

    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the text file.

        Args:
            **kwargs: Must include 'file_data' as bytes

        Returns:
            Dict containing extracted section content
        """
        file_data = kwargs.get('file_data')
        if not file_data:
            raise ValueError("Missing required 'file_data' parameter")

        section_data = self.extract_text_with_sections(file_data)
        return {"pages": section_data}

    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted text content.

        Args:
            content: Dictionary containing sections with extracted content

        Returns:
            Dict containing content with embeddings
        """
        pages = content.get("pages", [])
        processed_data = await self.process_pages(pages)
        return processed_data

    def _log_error(self, message: str, error: Exception) -> None:
        """Log an error with consistent format."""
        logging.error(f"{message}: {str(error)[:200]}", exc_info=True)
