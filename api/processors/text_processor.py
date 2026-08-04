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
        processed_data = await self.process_pages(section_data)
        logger.info(f"Completed processing sections: generated {len(processed_data.get('chunks', []))} chunks")

        if processed_data and "chunks" in processed_data:
            logger.info(f"Storing {len(processed_data['chunks'])} chunks in database for file {file_id}")
            try:
                await self.store.file_repo.update_file_status(int(file_id), "storing")
            except Exception:
                logger.debug("Non-fatal: could not update status to 'storing'")
            success = await self.store.file_repo.update_file_with_chunks(
                user_id=user_id,
                filename=filename,
                file_type="text",
                extracted_data=processed_data["chunks"]
            )
            logger.info(f"Database update with chunks {'successful' if success else 'failed'} for file {file_id}")

            if not success:
                logger.error(f"Failed to store chunks for file {file_id}")
                return {
                    "success": False,
                    "file_id": file_id,
                    "error": "Failed to store chunks",
                    "metadata": {
                        "processor_type": "text",
                        "section_count": len(section_data)
                    }
                }

        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "section_count": len(section_data),
                "chunk_count": len(processed_data.get("chunks", [])) if processed_data else 0,
                "processor_type": "text"
            }
        }

    async def process_pages(self, page_data: List[Dict]) -> Dict[str, Any]:
        """
        Process text sections: chunk text and generate embeddings incrementally.
        Same shape as PDFProcessor/DocxProcessor's process_pages so all three
        feed the same downstream storage path.
        """
        all_chunks = []
        BATCH_SIZE = 50
        total_sections = len(page_data)

        logger.info(f"Processing {total_sections} sections in batches of {BATCH_SIZE}")

        for batch_start in range(0, total_sections, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_sections)
            section_batch = page_data[batch_start:batch_end]
            batch_chunks = []

            logger.info(f"Processing sections {batch_start+1}-{batch_end} of {total_sections}")

            for section_item in section_batch:
                try:
                    section_content = section_item['text']
                    page_num = section_item['page_num']

                    if not section_content:
                        continue

                    text_chunks = chunk_text(section_content)
                    non_empty_chunks = [chunk['content'] for chunk in text_chunks if chunk['content'].strip()]

                    for chunk_content in non_empty_chunks:
                        batch_chunks.append({
                            'text': chunk_content,
                            'page_num': page_num,
                            'source_type': 'text'
                        })

                except Exception as e:
                    logger.error(f"Error processing section {section_item.get('page_num', 'unknown')}: {e}")

            if batch_chunks:
                chunk_texts = [chunk['text'] for chunk in batch_chunks]

                try:
                    logger.info(f"Generating embeddings for {len(chunk_texts)} chunks...")
                    chunk_embeddings = await get_text_embeddings_in_batches(chunk_texts, batch_size=50)
                except Exception as e:
                    logger.error(f"Embedding generation failed for batch: {e}")
                    raise ValueError(f"Failed to generate embeddings: {e}")

                if not chunk_embeddings or len(chunk_embeddings) != len(chunk_texts):
                    raise ValueError(f"Embedding count mismatch: expected {len(chunk_texts)}, got {len(chunk_embeddings)}")

                embedding_dim = len(chunk_embeddings[0]) if chunk_embeddings else 0
                if embedding_dim == 0:
                    raise ValueError("Embeddings have zero dimensions - model failed to generate valid vectors")

                logger.info(f"Generated {len(chunk_embeddings)} embeddings with dimension {embedding_dim}")

                for i, chunk in enumerate(batch_chunks):
                    chunk['embedding'] = chunk_embeddings[i] if i < len(chunk_embeddings) else None
                    chunk['metadata'] = {
                        'page': chunk['page_num'],
                        'source_type': 'text'
                    }

                all_chunks.extend(batch_chunks)

                batch_chunks = None
                chunk_texts = None
                chunk_embeddings = None
                gc.collect()
                logger.debug(f"Memory cleaned after batch {batch_start//BATCH_SIZE + 1}")

        logger.info(f"Completed processing all {total_sections} sections, generated {len(all_chunks)} total chunks")
        return {"chunks": all_chunks}

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
