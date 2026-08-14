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
        page_data = await self.extract_text_with_page_numbers(file_data, file_id=int(file_id))
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

    # Above this many vector drawing operations, a page's content is drawn
    # rather than written, so its text layer cannot account for what is on it.
    # Measured 2026-08-14: the goodman nomenclature diagram has 202, and pages
    # that are genuinely tables or prose have very few.
    VISION_VECTOR_DRAWINGS = int(os.getenv("VISION_VECTOR_DRAWINGS", "60"))

    def _text_layer_is_credible(self, page) -> bool:
        """Whether this page's text layer can arbitrate the vision output.

        True for tables and prose, where every character is present and only
        the order is wrong. False for figures, whose labels are drawn as vector
        art and never reach the text layer at all.
        """
        try:
            if len(page.get_drawings()) >= self.VISION_VECTOR_DRAWINGS:
                return False
            page_area = abs(page.rect.width * page.rect.height)
            if page_area:
                image_area = 0.0
                for img in page.get_images(full=True):
                    bbox = page.get_image_bbox(img)
                    if bbox:
                        image_area += abs(fitz.Rect(bbox).get_area())
                if image_area / page_area >= self.VISION_IMAGE_AREA:
                    return False
        except Exception:
            # If the page cannot be inspected, keep the stricter behaviour.
            return True
        return True

    async def _read_page_with_vision(self, page, text_layer: str):
        """Read a page with the vision model, and check its numbers.

        THE GUARDRAIL, AND THE PAGES IT MUST NOT BE APPLIED TO

        A vision model that invents a digit is the worst failure available
        here: a confident wrong refrigerant charge or torque value is a safety
        claim, not a typo. Measured 2026-08-12, a smaller model returned
        82/28/78 for a row that reads 82/28/80.

        So numbers are checked against the text layer. That rests on one
        assumption: the characters in the text layer are all CORRECT, and only
        their order is wrong. On a table that holds, which is what makes the
        layer a useful oracle and a useless transcription.

        ON A FIGURE IT IS FALSE, AND ENFORCING IT COST FOUR BENCHMARK QUESTIONS

        A leader-line nomenclature diagram draws its labels as vector art.
        Measured 2026-08-14 on page 7 of goodman_gszc7_service.pdf: 202 vector
        drawings, and the vision model read it correctly at 3,462 characters,
        including the two values the benchmark asks for. Sixteen of its numbers
        were absent from the text layer, so the whole page was rejected:

            '21'  (cabinet width, question 14)   in text layer: FALSE
            '410' (refrigerant,   question 15)   in text layer: FALSE

        Those numbers are not on the text layer to be verified against. The
        page was discarded and its garbled text kept, which is how question 13
        came to answer 1200 where the diagram says 1600. The check was
        rejecting precisely the pages vision exists for.

        So the hard reject applies only where its assumption holds: a page whose
        text layer is a credible record of what is on it. On a figure-dominant
        page the output is kept and flagged instead, because an unverifiable
        transcription still beats character soup that produces confident wrong
        answers.

        What this catches: a number that appears nowhere on a text-bearing page.
        What it cannot catch: a number that exists on the page and has been put
        in the wrong cell. That is why the model is chosen for accuracy first
        and this is a second line rather than a licence to use a cheap one.

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
            if self._text_layer_is_credible(page):
                logger.warning(
                    f"Vision output introduced {len(set(invented))} numbers absent "
                    f"from the text layer; keeping the text layer for this page"
                )
                return "", {"vision_rejected": "introduced numbers not on the page"}
            # A figure page's text layer is incomplete, not merely disordered,
            # so it cannot arbitrate. Keep the read and say it is unverified.
            logger.info(
                f"Vision output introduced {len(set(invented))} numbers absent from "
                f"the text layer, but this page is figure-dominant so the layer "
                f"cannot verify it; keeping the read and flagging it"
            )
            return markdown, {
                "vision_unverified_page": "figure page; text layer cannot verify",
                "vision_unverified_numbers": sorted(set(invented))[:10],
            }

        flags = {}
        if invented:
            # Kept rather than discarded, and recorded. Somebody reading a
            # citation deserves to know the page was transcribed rather than
            # read directly.
            flags["vision_unverified_numbers"] = sorted(set(invented))[:10]
        return markdown, flags

    async def extract_text_with_page_numbers(
        self, pdf_data: bytes, file_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts text from PDF data while capturing page numbers.
        Falls back to OCR (Tesseract) if a page contains only images.

        Pages read by the vision model are cached in `page_reads` as each one
        lands, and a re-run picks them back up. Without that, an ingest
        interrupted at page 57 of 58 re-bought all 57, at about three minutes
        each. Pass `file_id` to get the cache; omit it and this behaves exactly
        as it did before, which is what the non-ingest callers want.

        Args:
            pdf_data: PDF file data in bytes
            file_id: the file these pages belong to, if they are being ingested

        Returns:
            List of dictionaries with page numbers and text content
        """
        page_texts = []
        page_flags: Dict[int, Any] = {}
        # Pages whose text is vision markdown rather than PDF character soup.
        # The chunker needs to know: markdown has structure to respect, and a
        # text layer only has pipes that are not tables.
        vision_pages: set = set()
        read_by_vision = 0
        resumed = 0

        cached: Dict[int, Dict[str, Any]] = {}
        if file_id is not None:
            try:
                cached = await self.store.file_repo.cached_page_reads(int(file_id))
            except Exception as e:
                logger.warning(f"Could not read cached pages for file {file_id}: {e}")

        async def remember(n: int, text_value: str, source: str, flags: Optional[Dict] = None):
            if file_id is None:
                return
            await self.store.file_repo.save_page_read(
                int(file_id), n, text_value, source, flags
            )

        try:
            with fitz.open(stream=pdf_data, filetype="pdf") as doc:
                layer = {}
                needs_vision = []
                for page_num, page in enumerate(doc, 1):
                    layer[page_num] = sanitize_extracted_text(page.get_text("text"))
                    hit = cached.get(page_num)
                    if hit and hit.get("source") == "vision":
                        # Already paid for. Its flags come back with it, so a
                        # resumed page is no more trusted than a fresh one.
                        layer[page_num] = hit["text"]
                        vision_pages.add(page_num)
                        if hit.get("flags"):
                            page_flags[page_num] = hit["flags"]
                        resumed += 1
                        continue
                    if self._page_needs_vision(page, layer[page_num]):
                        needs_vision.append(page_num)

                if resumed:
                    logger.info(f"Resumed {resumed} page(s) already read by the vision model")

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
                            markdown, flags = await self._read_page_with_vision(doc[n - 1], layer[n])
                        # Saved here rather than after the gather, because the
                        # gather is the hour that keeps getting interrupted.
                        if markdown:
                            await remember(n, markdown, "vision", flags)
                        return n, (markdown, flags)

                    # return_exceptions, because a bare gather aborts on the
                    # first failure: it returns the moment one page raises,
                    # while its siblings are still mid-call. Every page already
                    # read but not yet saved was lost with it, and the whole
                    # document then fell into the handler below and extracted
                    # nothing. One page that the vision endpoint refused should
                    # cost that page its markdown and nothing else, because the
                    # text layer for it is still right there.
                    failed = 0
                    for outcome in await asyncio.gather(
                        *(read_one(n) for n in needs_vision), return_exceptions=True
                    ):
                        if isinstance(outcome, BaseException):
                            failed += 1
                            logger.warning(f"A page could not be read by vision: {outcome}")
                            continue
                        n, (markdown, flags) = outcome
                        if markdown:
                            layer[n] = markdown
                            vision_pages.add(n)
                            read_by_vision += 1
                            if flags:
                                page_flags[n] = flags
                    if failed:
                        logger.warning(
                            f"{failed} of {len(needs_vision)} pages fell back to the "
                            f"text layer because the vision call failed"
                        )

                for page_num in range(1, doc.page_count + 1):
                    text = layer[page_num]
                    if not text.strip():
                        hit = cached.get(page_num)
                        if hit and hit.get("source") == "ocr":
                            text = hit["text"]
                        else:
                            # Nothing from the text layer and nothing from
                            # vision. OCR is the last resort it always was.
                            pix = doc[page_num - 1].get_pixmap(dpi=300)
                            image = Image.open(io.BytesIO(pix.tobytes("png")))
                            text = pytesseract.image_to_string(image)
                            logger.debug(f"OCR extracted: {len(text)} chars")
                            await remember(page_num, text, "ocr")

                    page_texts.append({
                        "page_num": page_num,
                        "text": f"Page {page_num}\n{text.strip()}",  # Use 'text' for consistency
                        # Tells the chunker this page has structure worth
                        # respecting. Only pages the vision model actually
                        # produced markdown for: a page that fell back to its
                        # text layer is not markdown, and running the markdown
                        # chunker over PDF character soup would find pipes that
                        # are not tables.
                        "is_markdown": page_num in vision_pages,
                    })

            logger.info(
                f"Extracted {len(page_texts)} pages, {read_by_vision} of them read "
                f"by the vision model, {resumed} resumed from a previous attempt"
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
