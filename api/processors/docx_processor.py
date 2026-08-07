"""
DOCX processor module - Handles extraction and processing of Word documents.
Mirrors PDFProcessor's shape so chunks flow through the same downstream
storage/retrieval path. See pdf_processor.py for the reference pattern.
"""
import logging
import gc
from io import BytesIO
from typing import Dict, List, Any

from docx import Document as DocxDocument

from api.repositories.repository_manager import RepositoryManager
from api.processors.base_processor import FileProcessor
from api.services.llm_service import get_text_embeddings_in_batches
from api.services.contextualizer import add_context, embedding_text
from api.core.utils import chunk_text

logger = logging.getLogger(__name__)

HEADING_STYLES = {"Heading 1", "Heading 2", "Title"}


class DocxProcessor(FileProcessor):
    """
    Processor for Word (.docx) documents.
    Handles text extraction and embedding generation.
    """

    def __init__(self, store: RepositoryManager):
        """
        Initialize the DOCX processor.

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
        Process a DOCX file: extract text, generate embeddings.

        Args:
            file_data: Raw DOCX file data in bytes
            file_id: Database ID of the file
            user_id: ID of the user who owns the file
            filename: Name of the file
            **kwargs: Additional arguments

        Returns:
            Dictionary containing processing results
        """
        logger.info(f"Processing DOCX file: {filename} (ID: {file_id}, User: {user_id})")

        section_data = self.extract_text_with_sections(file_data)
        logger.info(f"DOCX extraction complete. Sections: {len(section_data)}")

        if not section_data:
            logger.error(f"Failed to extract content from DOCX: {filename}")
            return {
                "success": False,
                "file_id": file_id,
                "error": "Failed to extract content from DOCX",
                "metadata": {
                    "processor_type": "docx"
                }
            }

        logger.info(f"Starting section processing and embedding generation for file {filename}")
        try:
            await self.store.file_repo.update_file_status(int(file_id), "embedding")
        except Exception:
            logger.debug("Non-fatal: could not update status to 'embedding'")
        processed_data = await self.process_pages(section_data, file_name=filename)
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
                file_type="docx",
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
                        "processor_type": "docx",
                        "section_count": len(section_data)
                    }
                }

        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "section_count": len(section_data),
                "chunk_count": len(processed_data.get("chunks", [])) if processed_data else 0,
                "processor_type": "docx"
            }
        }

    async def process_pages(self, page_data: List[Dict], file_name: str = "") -> Dict[str, Any]:
        """
        Process DOCX sections: chunk text and generate embeddings incrementally.
        Named process_pages (not process_sections) to match the interface
        PDFProcessor uses, since both feed the same downstream chunk shape.
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
                            # The whole page, carried alongside each of its
                            # chunks. Storage groups by page to build the
                            # citation unit, and joining the chunks back
                            # together would duplicate their overlap.
                            'page_text': page_content,
                            'page_num': page_num,
                            'source_type': 'docx'
                        })

                except Exception as e:
                    logger.error(f"Error processing section {section_item.get('page_num', 'unknown')}: {e}")

            # Give each chunk a sentence of context before embedding it, so a
            # section is searchable by what it is about and not only by the
            # words that happen to be in it. No-op unless CONTEXTUALIZE_CHUNKS
            # is on, and never fatal: a document with no context is merely
            # harder to find, a document that failed to ingest is not there.
            if batch_chunks:
                try:
                    await add_context(batch_chunks, file_name)
                except Exception as ctx_error:
                    logger.warning(f"Contextualisation skipped: {ctx_error}")

            if batch_chunks:
                chunk_texts = [embedding_text(chunk) for chunk in batch_chunks]

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
                        'source_type': 'docx'
                    }

                all_chunks.extend(batch_chunks)

                batch_chunks = None
                chunk_texts = None
                chunk_embeddings = None
                gc.collect()
                logger.debug(f"Memory cleaned after batch {batch_start//BATCH_SIZE + 1}")

        logger.info(f"Completed processing all {total_sections} sections, generated {len(all_chunks)} total chunks")
        return {"chunks": all_chunks}

    def extract_text_with_sections(self, docx_data: bytes) -> List[Dict[str, Any]]:
        """
        Extracts text from DOCX data, split into logical sections at heading
        paragraphs (Heading 1/2/Title styles). Word has no fixed page concept
        outside of rendering, so sections stand in for "page_num" the same
        way PDF page numbers anchor a citation, "Section 3" instead of "p.3".
        Falls back to a single section if no headings are found. Table
        content is appended as trailing sections since python-docx exposes
        paragraphs and tables as separate lists, not interleaved in reading
        order, precise position isn't preserved, but the content isn't lost.

        Args:
            docx_data: DOCX file data in bytes

        Returns:
            List of dictionaries with section numbers and text content
        """
        try:
            doc = DocxDocument(BytesIO(docx_data))
        except Exception as e:
            logger.error(f"Error opening DOCX (may be corrupt or not a valid .docx): {e}", exc_info=True)
            return []

        sections: List[Dict[str, Any]] = []
        current_heading = None
        current_lines: List[str] = []

        def flush_section():
            content = "\n".join(current_lines).strip()
            if not content and current_heading:
                # A heading with no body text before the next heading (e.g. a
                # title immediately followed by the first section) would
                # otherwise be silently dropped, fall back to the heading
                # text itself so nothing gets lost.
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

        try:
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                if para.style is not None and para.style.name in HEADING_STYLES:
                    flush_section()
                    current_heading = text
                    current_lines = []
                else:
                    current_lines.append(text)
            flush_section()

            # Tables aren't interleaved with paragraphs in python-docx's flat
            # lists, append them as their own trailing sections rather than
            # dropping them.
            for t_idx, table in enumerate(doc.tables, 1):
                rows_text = []
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        rows_text.append(" | ".join(cells))
                if rows_text:
                    section_num = len(sections) + 1
                    sections.append({
                        "page_num": section_num,
                        "text": f"Table {t_idx}\n" + "\n".join(rows_text)
                    })

            if not sections:
                logger.warning("DOCX had no extractable paragraph or table text")

            return sections
        except Exception as e:
            logger.error(f"Error extracting DOCX sections: {e}", exc_info=True)
            return []

    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the DOCX file.

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
        Generate embeddings for the extracted DOCX content.

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
