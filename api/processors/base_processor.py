"""
Base processor classes and interfaces for file processing in the RAG pipeline.
These provide the foundation for all specific file type processors.

`embed_and_store_pages` below is the one place any of this actually happens.
PDF, DOCX and TXT each carried their own copy of that loop, identical apart
from a string, and the copies had already drifted into a silent bug once:
`page_text` was added to the PDF version on 2026-08-07 referring to a variable
the others did not have. Nothing said so, because a processor that produces
nothing used to still mark its file processed.
"""
from abc import ABC, abstractmethod
import gc
import hashlib
import logging
from typing import Dict, List, Any, Optional, Tuple

from api.core.utils import chunk_text, chunk_markdown
from api.services.llm_service import get_text_embeddings_in_batches

logger = logging.getLogger(__name__)

# Pages per batch. One batch is one database transaction, so this is also the
# resume granularity: a crash loses at most this many pages of work.
BATCH_SIZE = 50


class FileProcessor(ABC):
    """Abstract base class for all file processors."""

    async def embed_and_store_pages(
        self,
        page_data: List[Dict],
        *,
        file_id: int,
        user_id: int,
        filename: str,
        file_type: str,
    ) -> Dict[str, Any]:
        """Chunk, embed and store `page_data`, one batch at a time.

        WHY THIS WRITES AS IT GOES

        Everything used to be held in memory and written once at the very end,
        which cost two things a customer feels. A crash on page 490 of 500 threw
        away every page before it, because none of them existed anywhere yet,
        and the retry started again from page 1. And nothing in a document was
        searchable until all of it was, so a long PDF was unusable for as long
        as it took to finish rather than getting better as it went.

        Storing each batch fixes both at once, and the second one comes free:
        retrieval filters by workspace and never by processing status, so a
        chunk is findable the moment its transaction commits.

        It also makes the batching honest. The old loop batched the work but
        accumulated the results, holding every chunk and every 1024-dimension
        vector until the end, so peak memory still grew with the length of the
        document while a comment said it did not.

        RESUMING

        Pages already in the database are skipped rather than rewritten. A
        batch is one transaction, so a page is either wholly stored or absent,
        which is what lets `stored_page_numbers` answer this without a progress
        column that could disagree with the rows.

        NOT PAYING TWICE FOR THE SAME TEXT

        Embedding is the one part of this that costs money per call, and it is
        a pure function of its input. Each chunk's text is hashed and any vector
        this organization already has is reused, so only genuinely new text is
        sent to the embedder.

        Returns {stored_chunks, stored_pages, skipped_pages, reused_embeddings,
        deduplicated_in_batch, total_pages}.
        """
        total_pages = len(page_data)
        already = await self.store.file_repo.stored_page_numbers(int(file_id))
        pending = [p for p in page_data if p.get("page_num") not in already]

        if already:
            logger.info(
                f"Resuming {filename}: {len(already)} page(s) already stored, "
                f"{len(pending)} to go"
            )

        stored_chunks = 0
        stored_pages = 0
        reused_embeddings = 0
        deduplicated_in_batch = 0

        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[batch_start:batch_start + BATCH_SIZE]
            batch_chunks: List[Dict[str, Any]] = []

            logger.info(
                f"Processing pages {batch_start + 1}-{batch_start + len(batch)} "
                f"of {len(pending)} pending ({total_pages} total)"
            )

            for page_item in batch:
                try:
                    page_content = page_item.get("text")
                    page_num = page_item.get("page_num")
                    if not page_content:
                        continue

                    # A page the vision model read is markdown, and markdown is
                    # chunked on its own structure. The general splitter counts
                    # to 400 and cuts wherever it lands, which on a table means
                    # mid-row and away from the header: measured 2026-08-13, it
                    # turned a reconstructed charging chart into three chunks of
                    # which all three were unanswerable. See chunk_markdown.
                    chunker = chunk_markdown if page_item.get("is_markdown") else chunk_text
                    for chunk in chunker(page_content):
                        content = chunk.get("content") or ""
                        if not content.strip():
                            continue
                        unit = {
                            "text": content,
                            # The whole page, carried alongside each of its
                            # chunks. Storage groups by page to build the
                            # citation unit, and joining the chunks back
                            # together would duplicate their overlap.
                            "page_text": page_content,
                            "page_num": page_num,
                            "source_type": file_type,
                        }
                        # Anything the extractor wants to say about how this
                        # page was read, on its way to the segment's meta_data.
                        # Today that is only the vision model's verification
                        # flags; a processor with nothing to say sets nothing.
                        if page_item.get("flags"):
                            unit["flags"] = page_item["flags"]
                        batch_chunks.append(unit)
                except Exception as e:
                    logger.error(
                        f"Error processing page {page_item.get('page_num', 'unknown')}: {e}"
                    )

            if not batch_chunks:
                continue

            # What gets embedded is the chunk's text and nothing else.
            #
            # A sentence of model-written context used to go in front of it,
            # the idea being that a passage is then searchable by what it is
            # about rather than only by the words on it. Measured 2026-08-15
            # with the mechanism complete for the first time and on a corpus
            # whose vectors were correct: it retrieved no more than this does
            # and ranked slightly worse. The reasons are in the overview, and
            # the short one is that context describing the document cannot
            # separate documents that are all alike.
            chunk_texts = [chunk["text"] for chunk in batch_chunks]
            hashes = [
                hashlib.sha256(t.encode("utf-8")).hexdigest() for t in chunk_texts
            ]
            for chunk, content_hash in zip(batch_chunks, hashes):
                chunk["content_hash"] = content_hash

            # Embedding is a pure function of its input, so text this
            # organization has embedded before has a vector already sitting in
            # the database. Re-uploading a corrected policy, or the same terms
            # appearing across twenty contracts, used to be billed every time.
            #
            # Never fatal. A lookup that fails means paying for embeddings we
            # could have had free, which is exactly today's behaviour, and an
            # upload must not fail because an optimisation did.
            try:
                known = await self.store.file_repo.embeddings_for_hashes(
                    int(file_id), sorted(set(hashes))
                )
            except Exception as e:
                logger.warning(f"Could not check for reusable embeddings: {e}")
                known = {}

            # Unique texts, not unique positions. A batch routinely contains the
            # same text twice, from a repeated header or a boilerplate
            # paragraph, and sending each occurrence separately pays twice for
            # one answer within a single call. Worse, an embedder with any
            # sampling in it can return two different vectors for one hash,
            # which then makes "the same text has the same vector" false in the
            # very table the reuse lookup reads from.
            from_database = set(known)
            wanted = []
            seen = set()
            for content_hash, content in zip(hashes, chunk_texts):
                if content_hash in known or content_hash in seen:
                    continue
                seen.add(content_hash)
                wanted.append((content_hash, content))

            if wanted:
                try:
                    fresh = await get_text_embeddings_in_batches(
                        [content for _, content in wanted], batch_size=50
                    )
                except Exception as e:
                    logger.error(f"Embedding generation failed for batch: {e}")
                    raise ValueError(f"Failed to generate embeddings: {e}")

                if not fresh or len(fresh) != len(wanted):
                    raise ValueError(
                        f"Embedding count mismatch: expected {len(wanted)}, got "
                        f"{len(fresh) if fresh else 0}"
                    )
                if not fresh[0]:
                    raise ValueError(
                        "Embeddings have zero dimensions - model failed to generate valid vectors"
                    )
                for (content_hash, _), vector in zip(wanted, fresh):
                    known[content_hash] = vector

            # Two different savings, counted apart because they mean different
            # things. One is "this organization already owns this vector",
            # which is the re-uploaded document. The other is "this batch
            # contains the same text more than once", which is a repeated
            # header. Adding them together would make either number unreadable.
            from_db = sum(1 for h in hashes if h in from_database)
            within_batch = len(chunk_texts) - len(wanted) - from_db
            if from_db or within_batch:
                logger.info(
                    f"Embedded {len(wanted)} of {len(chunk_texts)} chunks: "
                    f"{from_db} already stored, {within_batch} repeated in this batch"
                )
            reused_embeddings += from_db
            deduplicated_in_batch += within_batch

            embeddings = [known.get(h) for h in hashes]

            for chunk, embedding in zip(batch_chunks, embeddings):
                chunk["embedding"] = embedding
                chunk["metadata"] = {"page": chunk["page_num"], "source_type": file_type}

            # Committed here rather than collected. Everything above this line
            # is thrown away if the process dies; everything below it survives.
            ok = await self.store.file_repo.update_file_with_chunks(
                user_id=user_id,
                filename=filename,
                file_type=file_type,
                extracted_data=batch_chunks,
                file_id=int(file_id),
                # Not finished until the last batch lands, and the caller says
                # so, not this loop.
                mark_processed=False,
            )
            if not ok:
                raise ValueError(
                    f"Failed to store a batch of {len(batch_chunks)} chunks for {filename}"
                )

            stored_chunks += len(batch_chunks)
            stored_pages += len({c["page_num"] for c in batch_chunks})

            batch_chunks = None
            chunk_texts = None
            embeddings = None
            gc.collect()

        logger.info(
            f"Stored {stored_chunks} chunk(s) across {stored_pages} page(s) of "
            f"{filename}, skipping {len(already)} already stored"
        )
        return {
            "stored_chunks": stored_chunks,
            "stored_pages": stored_pages,
            "skipped_pages": len(already),
            "reused_embeddings": reused_embeddings,
            "deduplicated_in_batch": deduplicated_in_batch,
            "total_pages": total_pages,
        }
    
    @abstractmethod
    async def process(self, 
                     user_id: str, 
                     file_id: str, 
                     filename: str, 
                     file_url: str, 
                     **kwargs) -> Dict[str, Any]:
        """
        Process the file and return results.
        
        Args:
            user_id: User ID
            file_id: File ID
            filename: Name of the file
            file_url: URL to access the file
            **kwargs: Additional parameters specific to the processor
            
        Returns:
            Dict containing processing results
        """
        pass
    
    @abstractmethod
    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract raw content from the file.
        
        Returns:
            Dict containing extracted content
        """
        pass
        
    @abstractmethod
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted content.

        Args:
            content: Extracted content

        Returns:
            Dict containing content with embeddings
        """
        pass

    def _log_error(self, message: str, error: Exception, exc_info: bool = True) -> None:
        """Utility method for consistent error logging."""
        logger.error(f"{message}: {error}", exc_info=exc_info)
