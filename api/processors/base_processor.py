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
import logging
from typing import Dict, List, Any, Optional, Tuple

from api.core.utils import chunk_text
from api.services.contextualizer import add_context, embedding_text
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

        Returns {stored_chunks, stored_pages, skipped_pages, total_pages}.
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

                    for chunk in chunk_text(page_content):
                        content = chunk.get("content") or ""
                        if not content.strip():
                            continue
                        batch_chunks.append({
                            "text": content,
                            # The whole page, carried alongside each of its
                            # chunks. Storage groups by page to build the
                            # citation unit, and joining the chunks back
                            # together would duplicate their overlap.
                            "page_text": page_content,
                            "page_num": page_num,
                            "source_type": file_type,
                        })
                except Exception as e:
                    logger.error(
                        f"Error processing page {page_item.get('page_num', 'unknown')}: {e}"
                    )

            if not batch_chunks:
                continue

            # Give each chunk a sentence of context before embedding it, so a
            # page is searchable by what it is about and not only by the words
            # that happen to be on it. No-op unless CONTEXTUALIZE_CHUNKS is on,
            # and never fatal: a document with no context is merely harder to
            # find, a document that failed to ingest is not there at all.
            try:
                await add_context(batch_chunks, filename)
            except Exception as ctx_error:
                logger.warning(f"Contextualisation skipped: {ctx_error}")

            chunk_texts = [embedding_text(chunk) for chunk in batch_chunks]
            try:
                embeddings = await get_text_embeddings_in_batches(chunk_texts, batch_size=50)
            except Exception as e:
                logger.error(f"Embedding generation failed for batch: {e}")
                raise ValueError(f"Failed to generate embeddings: {e}")

            if not embeddings or len(embeddings) != len(chunk_texts):
                raise ValueError(
                    f"Embedding count mismatch: expected {len(chunk_texts)}, got "
                    f"{len(embeddings) if embeddings else 0}"
                )
            if not embeddings[0]:
                raise ValueError(
                    "Embeddings have zero dimensions - model failed to generate valid vectors"
                )

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
