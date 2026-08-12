"""
PDF processor module - Handles extraction and processing of PDF documents.
"""
import logging
import re
from ..core.utils import sanitize_extracted_text
import os
import asyncio
import gc
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO
import json
from datetime import datetime

import pytesseract
from PIL import Image
import io
import fitz  # PyMuPDF
from api.repositories.repository_manager import RepositoryManager
from api.processors.base_processor import FileProcessor
from api.services.llm_service import (
    get_text_embeddings_in_batches,
    read_page,
    VISION_CONCURRENCY,
    VISION_DPI,
)
from api.services.contextualizer import add_context, embedding_text
from api.services.outline import extract_pdf_outline
from api.core.utils import chunk_text
logger = logging.getLogger(__name__)

class PDFProcessor(FileProcessor):
    """
    Processor for PDF documents.
    Handles text extraction and embedding generation.
    """
    
    def __init__(self, store: RepositoryManager):
        """
        Initialize the PDF processor.
        
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
        Process a PDF file: extract text, generate embeddings, create key concepts.
        
        Args:
            file_data: Raw PDF file data in bytes
            file_id: Database ID of the file
            user_id: ID of the user who owns the file
            filename: Name of the file
            **kwargs: Additional arguments
            
        Returns:
            Dictionary containing processing results
        """
        # Extract additional parameters
        language = kwargs.get('language', 'English')
        comprehension_level = kwargs.get('comprehension_level', 'Beginner')
        
        logger.info(f"Processing PDF file: {filename} (ID: {file_id}, User: {user_id}, Language: {language}, Level: {comprehension_level})")
        
        # Extract text from PDF with page numbers
        page_data = await self.extract_text_with_page_numbers(file_data)
        logger.info(f"PDF extraction complete. Pages: {len(page_data)}")

        # And what the document says it contains. Cheap, one call on a document
        # already being opened, and the only thing that lets an answer cite the
        # section that defines a rule rather than a page that mentions it.
        try:
            outline = extract_pdf_outline(file_data)
            if outline:
                await self.store.file_repo.set_outline(int(file_id), outline)
        except Exception as outline_error:
            logger.warning(f"Outline extraction skipped for {filename}: {outline_error}")
        
        if not page_data:
            logger.error(f"Failed to extract content from PDF: {filename}")
            return {
                "success": False,
                "file_id": file_id,
                "error": "Failed to extract content from PDF",
                "metadata": {
                    "processor_type": "pdf"
                }
            }
            
        # Process extracted pages and generate embeddings
        logger.info(f"Starting page processing and embedding generation for file {filename}")
        # Update status to 'embedding' to support REST polling progress
        try:
            await self.store.file_repo.update_file_status(int(file_id), "embedding")
        except Exception:
            logger.debug("Non-fatal: could not update status to 'embedding'")

        # Chunks are written batch by batch inside this call, so a crash keeps
        # the pages it already reached and the answer can already cite them.
        result = await self.embed_and_store_pages(
            page_data,
            file_id=int(file_id),
            user_id=user_id,
            filename=filename,
            file_type="pdf",
        )

        # A document that extracted nothing is a failure, not a success. Every
        # per-item exception is caught and logged so one bad page cannot lose a
        # whole document, which is right, but it meant a processor could catch
        # every page, produce zero chunks, and still mark the file processed.
        # The file then sits in the list looking ready and answers nothing, and
        # the only trace is a log line nobody reads.
        #
        # This exact shape shipped twice: once when the storage layer read a
        # structure no processor produced, and again on 2026-08-07 when
        # page_text was added referring to a variable that existed in one
        # processor and not another. Both were silent, and both are the reason
        # that loop now lives in one place.
        #
        # Counted across the whole document, not this attempt: a resumed run
        # that finds every page already stored does no work and is finished,
        # not empty.
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
                "metadata": {"processor_type": "pdf"},
            }

        return {
            "success": True,
            "file_id": file_id,
            "metadata": {
                "page_count": len(page_data),
                "chunk_count": result["stored_chunks"],
                "resumed_pages": result["skipped_pages"],
                "processor_type": "pdf"
            }
        }
    
    # A page goes to the vision model only when the text layer has probably
    # failed it. Measured on the 433-page HVAC corpus: reading every page would
    # take 17 hours at 146 seconds a page, and roughly three quarters of those
    # pages are prose the text layer already handles at 4/4 on citations.
    #
    # Two signals, both free to compute before deciding:
    #
    #   digits      a table is mostly numbers. Prose is not. 12% of characters
    #               being digits separates the charging charts and spec tables
    #               in this corpus from the paragraphs around them.
    #   image area  a page that is mostly picture is a diagram, and the words
    #               on it are labels whose meaning comes from where they point.
    #
    # Deliberately not "does the page have a table", because detecting that
    # reliably is the same problem as reading it.
    VISION_DIGIT_RATIO = float(os.getenv("VISION_DIGIT_RATIO", "0.12"))
    VISION_IMAGE_AREA = float(os.getenv("VISION_IMAGE_AREA", "0.35"))

    def _page_needs_vision(self, page, text: str) -> bool:
        """Whether the text layer probably lost this page."""
        stripped = text.strip()
        if not stripped:
            # No text at all. This used to be the only case OCR handled, and on
            # 433 pages of real service manuals it never once fired.
            return True

        digits = sum(c.isdigit() for c in stripped)
        if digits / len(stripped) >= self.VISION_DIGIT_RATIO:
            return True

        try:
            page_area = page.rect.width * page.rect.height
            image_area = 0.0
            for img in page.get_images(full=True):
                r = page.get_image_bbox(img)
                image_area += (r[2] - r[0]) * (r[3] - r[1])
            if page_area and image_area / page_area >= self.VISION_IMAGE_AREA:
                return True
        except Exception:
            # A page whose image geometry cannot be read is not worth failing
            # ingestion over; the text layer still applies.
            logger.debug("Could not measure image area", exc_info=True)

        return False

    async def _read_page_with_vision(self, page, text_layer: str):
        """Read a page with the vision model, and check its numbers.

        THE GUARDRAIL, AND WHAT IT CAN AND CANNOT DO

        A vision model that invents a digit is the worst failure available
        here: a confident wrong refrigerant charge or torque value is a safety
        claim, not a typo. This is not theoretical. Measured on 2026-08-12, a
        smaller model returned 82/28/78 for a row that reads 82/28/80.

        So every number the model produces is checked against the text layer.
        The characters in that layer are correct even when their order is
        destroyed, which is exactly what makes it useful as a check and useless
        as a transcription.

        What this catches: a number that appears nowhere on the page. What it
        cannot catch: a number that exists on the page and has been put in the
        wrong cell, which is what the smaller model actually did. That is why
        the model is chosen for accuracy first and the check is a second line
        rather than the reason to trust a cheap one.

        Returns (markdown, flags). Empty markdown means fall back.
        """
        try:
            pix = page.get_pixmap(dpi=VISION_DPI)
            markdown = await read_page(pix.tobytes("png"), hint=text_layer)
        except Exception as e:
            logger.warning(f"Could not render page for vision: {type(e).__name__}")
            return "", {}

        if not markdown:
            return "", {}

        source_numbers = set(re.findall(r"\d+(?:\.\d+)?", text_layer or ""))
        invented = [
            n for n in re.findall(r"\d+(?:\.\d+)?", markdown)
            if n not in source_numbers
        ]
        # A handful of stray numbers are ordinary: a markdown table adds none,
        # but a model may spell out a figure caption or renumber a list. Many
        # of them means it is writing rather than reading.
        if len(set(invented)) > 5:
            logger.warning(
                f"Vision output introduced {len(set(invented))} numbers absent from "
                f"the text layer; keeping the text layer for this page"
            )
            return "", {"vision_rejected": "introduced numbers not on the page"}

        flags = {}
        if invented:
            # Kept rather than discarded, and recorded. Somebody reading a
            # citation deserves to know the page was transcribed rather than
            # read directly.
            flags["vision_unverified_numbers"] = sorted(set(invented))[:10]
        return markdown, flags

    async def extract_text_with_page_numbers(self, pdf_data: bytes) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF data while capturing page numbers.
        Falls back to OCR (Tesseract) if a page contains only images.
        
        Args:
            pdf_data: PDF file data in bytes

        Returns:
            List of dictionaries with page numbers and text content
        """
        page_texts = []
        page_flags: Dict[int, Any] = {}
        read_by_vision = 0
        try:
            with fitz.open(stream=pdf_data, filetype="pdf") as doc:
                layer = {}
                needs_vision = []
                for page_num, page in enumerate(doc, 1):
                    layer[page_num] = sanitize_extracted_text(page.get_text("text"))
                    if self._page_needs_vision(page, layer[page_num]):
                        needs_vision.append(page_num)

                # Concurrently, because a page takes about 146 seconds and
                # doing 112 of them one after another is four and a half hours
                # for one corpus. The cap exists because the far end is a
                # shared inference endpoint and this runs inside a worker slot
                # that other tenants are queued behind.
                if needs_vision:
                    logger.info(
                        f"Reading {len(needs_vision)} of {doc.page_count} pages with "
                        f"the vision model, {VISION_CONCURRENCY} at a time"
                    )
                    gate = asyncio.Semaphore(VISION_CONCURRENCY)

                    async def read_one(n: int):
                        async with gate:
                            return n, await self._read_page_with_vision(doc[n - 1], layer[n])

                    for n, (markdown, flags) in await asyncio.gather(
                        *(read_one(n) for n in needs_vision)
                    ):
                        if markdown:
                            layer[n] = markdown
                            read_by_vision += 1
                            if flags:
                                page_flags[n] = flags

                for page_num in range(1, doc.page_count + 1):
                    text = layer[page_num]
                    if not text.strip():
                        # Nothing from the text layer and nothing from vision.
                        # OCR is the last resort it always was.
                        pix = doc[page_num - 1].get_pixmap(dpi=300)
                        image = Image.open(io.BytesIO(pix.tobytes("png")))
                        text = pytesseract.image_to_string(image)
                        logger.debug(f"OCR extracted: {len(text)} chars")

                    page_texts.append({
                        "page_num": page_num,
                        "text": f"Page {page_num}\n{text.strip()}"  # Use 'text' for consistency
                    })

            logger.info(
                f"Extracted {len(page_texts)} pages, {read_by_vision} of them read "
                f"by the vision model"
            )
            return page_texts
        except Exception as e:
            logger.error(f"Error extracting text (Tesseract fallback): {e}", exc_info=True)
            return []
    
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the PDF file.
        
        Args:
            **kwargs: Must include 'file_data' as bytes
            
        Returns:
            Dict containing extracted page content
        """
        file_data = kwargs.get('file_data')
        if not file_data:
            raise ValueError("Missing required 'file_data' parameter")
            
        page_data = self.extract_text_with_page_numbers(file_data)
        return {"pages": page_data}
    
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted PDF content.
        
        Args:
            content: Dictionary containing pages with extracted content
            
        Returns:
            Dict containing content with embeddings
        """
        pages = content.get("pages", [])
        processed_data = await self.process_pages(pages)
        return processed_data
    
    def _log_error(self, message: str, error: Exception) -> None:
        """Log an error with consistent format."""
        logging.error(f"{message}: {str(error)[:200]}", exc_info=True)
