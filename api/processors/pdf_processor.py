"""
PDF processor module - Handles extraction and processing of PDF documents.
Uses PyMuPDF for text extraction with chunked embeddings and concept extraction.
"""
import logging
import asyncio
import fitz  # PyMuPDF
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from api.processors.base_processor import FileProcessor
from api.repositories.repository_manager import RepositoryManager
from api.services.llm_service import llm_service
from api.services.embedding_service import embedding_service
from api.llm_compat import generate_key_concepts_dspy

logger = logging.getLogger(__name__)

# Concurrency controls
_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_EMBED_CONCURRENCY = 5
_CONCEPT_CONCURRENCY = 5


async def process_pdf(file_data: bytes, file_id: int, user_id: int, filename: str = "", **kwargs) -> Dict[str, Any]:
    """
    Process a PDF file through the entire pipeline.
    
    Args:
        file_data: Raw PDF bytes
        file_id: Unique file identifier
        user_id: User who owns the file
        filename: Original filename (optional)
        **kwargs: Additional processing options
        
    Returns:
        Dictionary with processing results and status
    """
    try:
        repo = RepositoryManager()
        async with PDFProcessor(repo) as processor:
            return await processor.process(
                file_data=file_data,
                file_id=file_id,
                user_id=user_id,
                filename=filename,
                **kwargs
            )
    except Exception as e:
        logger.error(f"PDF processing failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Failed to process PDF: {str(e)}",
            "content": None,
            "key_concepts": None,
            "metadata": {}
        }


class PDFProcessor(FileProcessor[Dict[str, Any]]):
    """
    Processor for PDF files with support for text extraction, chunking,
    embeddings, and concept extraction.
    """

    def __init__(self, store: RepositoryManager):
        super().__init__()
        self.store = store
        self.semaphore = asyncio.Semaphore(_EMBED_CONCURRENCY)
        self.concept_semaphore = asyncio.Semaphore(_CONCEPT_CONCURRENCY)
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Cleanup any resources if needed
        pass

    async def extract_content(self, **kwargs) -> Dict[str, Any]:
        """
        Extract text content from a PDF file with page-level metadata.
        
        Args:
            file_data: Raw PDF bytes
            file_id: Unique file identifier
            user_id: User who owns the file
            filename: Original filename (optional)
            
        Returns:
            Dictionary with extracted content and metadata
        """
        file_data = kwargs.get("file_data")
        file_id = kwargs.get("file_id")
        user_id = kwargs.get("user_id")
        filename = kwargs.get("filename", "")
        language = kwargs.get("language", "en")

        if not all([file_data, file_id, user_id]):
            return {
                "success": False,
                "error": "Missing required parameters: file_data, file_id, or user_id",
                "text": "",
                "pages": [],
                "segments": []
            }

        try:
            # Process PDF in a thread to avoid blocking
            def _extract_pages():
                with fitz.open(stream=BytesIO(file_data), filetype="pdf") as doc:
                    pages = []
                    for page_num in range(len(doc)):
                        try:
                            page = doc.load_page(page_num)
                            page_text = page.get_text()
                            pages.append({
                                "page_number": page_num + 1,
                                "text": page_text,
                                "metadata": {
                                    "width": page.rect.width,
                                    "height": page.rect.height,
                                    "rotation": page.rotation
                                }
                            })
                        except Exception as e:
                            logger.warning(f"Skipping page {page_num+1} in {filename}: {e}")
                    return pages

            pages = await asyncio.get_event_loop().run_in_executor(
                _CPU_EXECUTOR, _extract_pages
            )

            if not pages:
                return {
                    "success": False,
                    "error": "No extractable content found in PDF",
                    "text": "",
                    "pages": [],
                    "segments": []
                }

            # Convert pages to segments format for consistency with other processors
            segments = []
            full_text = []
            
            for page in pages:
                page_num = page["page_number"]
                page_text = page["text"].strip()
                if not page_text:
                    continue
                    
                full_text.append(f"\n\nPage {page_num}:\n{page_text}")
                segments.append({
                    "text": page_text,
                    "start": page_num - 1,  # Using page number as position
                    "end": page_num,
                    "page_number": page_num,
                    "metadata": page["metadata"]
                })

            full_text = "".join(full_text).strip()
            
            return {
                "success": True,
                "text": full_text,
                "segments": segments,
                "pages": pages,
                "total_pages": len(pages),
                "metadata": {
                    "filename": filename,
                    "file_size": len(file_data),
                    "extraction_method": "pymupdf",
                    "file_id": file_id,
                    "user_id": user_id,
                    "source_type": "pdf",
                    "language": language,
                    "processing_status": "completed"
                },
            }

        except Exception as e:
            logger.error(f"Error extracting text from PDF {filename}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to extract text: {e}",
                "text": "",
                "pages": [],
                "segments": []
            }

    async def _batch_embed_texts(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """Embed texts in batches with concurrency control."""
        all_embeddings = []

        async def embed(text: str):
            async with self.semaphore:
                try:
                    return await embedding_service.get_embedding(text)
                except Exception as e:
                    logger.error(f"Embedding failed: {e}")
                    return [0.0] * 1536  # Return zero vector on failure

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            results = await asyncio.gather(*[embed(t) for t in batch])
            all_embeddings.extend(results)

        return all_embeddings

    async def generate_embeddings(self, content: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Generate embeddings for the extracted content with chunking and overlap.
        
        Args:
            content: Dictionary containing extracted text and segments
            chunk_size: Maximum size of each text chunk (default: 1000 chars)
            chunk_overlap: Overlap between chunks (default: 200 chars)
            
        Returns:
            Dictionary with content and generated embeddings
        """
        if not content.get("success"):
            return content

        try:
            # Use segments if available, otherwise fall back to full text
            segments = content.get("segments", [])
            if not segments:
                text = content.get("text", "")
                if not text.strip():
                    return {"success": False, "error": "No text content to embed"}
                segments = [{"text": text, "start": 0, "end": len(text)}]

            # Chunk the text with overlap
            chunks = self._split_text_with_overlap(
                segments,
                chunk_size=kwargs.get("chunk_size", 1000),
                chunk_overlap=kwargs.get("chunk_overlap", 200)
            )
            
            if not chunks:
                return {"success": False, "error": "No valid text chunks produced for embeddings"}

            # Generate embeddings in batches with concurrency control
            texts = [c["text"] for c in chunks]
            embeddings = await self._batch_embed_texts(texts)

            # Combine chunks with their embeddings
            processed_segments = []
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                processed_segments.append({
                    **chunk,
                    "embedding": emb,
                    "chunk_index": i,
                    "token_count": len(chunk["text"].split()),
                })

            return {
                "success": True,
                "processed_segments": processed_segments,
                "total_chunks": len(processed_segments),
                **{k: v for k, v in content.items() if k not in ["processed_segments", "total_chunks"]}
            }

        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to generate embeddings: {e}",
                **{k: v for k, v in content.items() if k != "error"}
            }

    def _split_text_with_overlap(self, segments: List[Dict[str, Any]], chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
        """
        Split text segments into chunks with overlap, preserving metadata.
        
        Args:
            segments: List of text segments with metadata
            chunk_size: Maximum size of each chunk
            chunk_overlap: Number of characters to overlap between chunks
            
        Returns:
            List of chunks with preserved metadata
        """
        chunks = []
        buffer = []
        current_length = 0
        current_start = None
        
        for seg in segments:
            seg_text = seg.get("text", "")
            if not seg_text.strip():
                continue
                
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", seg_start + len(seg_text))
            
            if current_start is None:
                current_start = seg_start
                
            # If adding this segment would exceed chunk size, finalize current chunk
            if current_length + len(seg_text) > chunk_size and buffer:
                chunk_text = " ".join(buffer)
                chunks.append({
                    "text": chunk_text,
                    "start": current_start,
                    "end": seg_start,
                    "metadata": {
                        "chunk_number": len(chunks) + 1,
                        "source": "pdf",
                        **seg.get("metadata", {})
                    }
                })
                
                # Prepare overlap for next chunk
                overlap_text = " ".join(buffer[-(chunk_overlap // 20):])  # Approximate overlap in words
                buffer = [overlap_text] if overlap_text else []
                current_length = len(overlap_text)
                current_start = seg_start
            
            buffer.append(seg_text)
            current_length += len(seg_text) + 1  # +1 for space
        
        # Add remaining text as final chunk
        if buffer:
            chunk_text = " ".join(buffer)
            chunks.append({
                "text": chunk_text,
                "start": current_start,
                "end": segments[-1].get("end", current_start + len(chunk_text)) if segments else current_start + len(chunk_text),
                "metadata": {
                    "chunk_number": len(chunks) + 1,
                    "source": "pdf",
                    **({} if not segments else segments[-1].get("metadata", {}))
                }
            })
            
        return chunks

    async def generate_key_concepts(self, content: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        Generate key concepts from document content using LLM.
        
        Args:
            content: Dictionary containing processed segments and metadata
            
        Returns:
            List of key concepts with relevance scores
        """
        if not content.get("success"):
            return []

        try:
            # Use processed segments if available, otherwise fall back to full text
            segments = content.get("processed_segments", [])
            if not segments:
                text = content.get("text", "")
                if not text.strip():
                    return []
                segments = [{"text": text, "start": 0, "end": len(text)}]

            all_concepts = []
            
            async def process_chunk(chunk: Dict[str, Any]):
                async with self.concept_semaphore:
                    try:
                        concepts = await generate_key_concepts_dspy(chunk["text"]) or []
                        for concept in concepts:
                            concept.update({
                                "file_id": content["metadata"]["file_id"],
                                "user_id": content["metadata"]["user_id"],
                                "source_type": "pdf",
                                "source_reference": f"Page {chunk.get('page_number', 'unknown')}",
                                "start": chunk.get("start"),
                                "end": chunk.get("end"),
                                "chunk_index": chunk.get("chunk_index"),
                                "metadata": {
                                    **chunk.get("metadata", {}),
                                    "extraction_method": "llm"
                                }
                            })
                        return concepts
                    except Exception as e:
                        logger.error(f"Concept extraction failed for chunk: {e}")
                        return []

            # Process chunks in parallel with concurrency control
            tasks = [process_chunk(chunk) for chunk in segments]
            results = await asyncio.gather(*tasks)
            all_concepts = [concept for sublist in results for concept in sublist]
            
            # Sort by relevance score (descending)
            return sorted(
                all_concepts,
                key=lambda x: float(x.get("relevance", 0)),
                reverse=True
            )

        except Exception as e:
            logger.error(f"Error in generate_key_concepts: {e}", exc_info=True)
            return []
