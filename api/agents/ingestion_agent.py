"""
Ingestion Agent for processing and normalizing various content types
with per-chunk summarization for ultra-long PDFs and YouTube videos.
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, Callable, Awaitable, List, Union

from api.agents.base_agent import BaseAgent, AgentConfig, AgentError
from api.agents.agent_factory import agent
from api.agents.summarization_agent import SummarizationAgent, SummarizationConfig
from api.utils.language_utils import validate_language
from api.processors import process_pdf, process_youtube, process_text, process_url
from api.services.embedding_service import embedding_service
from api.repositories import get_repository_manager
from api.repositories.async_file_repository import AsyncFileRepository

logger = logging.getLogger(__name__)


class IngestionConfig(AgentConfig):
    """Configuration for the Ingestion Agent."""
    max_chunk_size: int = 4000
    chunk_overlap: int = 200
    supported_types: list = ["pdf", "youtube", "text", "url"]
    timeout: int = 300
    generate_embeddings: bool = True
    embedding_provider: Optional[str] = None
    embedding_batch_size: int = 10


@agent(
    name="ingestion",
    description="Handles content ingestion from PDFs, YouTube, text, and URLs with per-chunk summarization",
    version="1.0.0",
    is_dspy_agent=False,
    config={
        "max_chunk_size": 4000,
        "chunk_overlap": 200,
        "supported_types": ["pdf", "youtube", "text", "url"],
        "timeout": 300,
        "generate_embeddings": True,
        "embedding_provider": None,
        "embedding_batch_size": 10
    }
)
class IngestionAgent(BaseAgent[IngestionConfig]):
    """Agent responsible for ingesting and normalizing content with scalable summarization."""

    def __init__(self, config: Optional[Union[Dict[str, Any], AgentConfig, IngestionConfig]] = None):
        super().__init__(config or IngestionConfig())
        if isinstance(self.config, AgentConfig) and not isinstance(self.config, IngestionConfig):
            self.config = IngestionConfig(**self.config.dict())
        elif not isinstance(self.config, IngestionConfig):
            self.config = IngestionConfig()

        self._processors: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "pdf": self._process_pdf,
            "youtube": self._process_youtube,
            "text": self._process_text,
            "url": self._process_url
        }
        self.summarization_agent = SummarizationAgent(SummarizationConfig())

    async def _process_pdf(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process PDF content."""
        file_data = input_data.get("content")
        
        # Handle file path or direct content
        if not file_data and "file_path" in input_data:
            try:
                with open(str(input_data["file_path"]), "rb") as f:
                    file_data = f.read()
            except (IOError, OSError) as e:
                raise AgentError(f"Failed to read PDF file: {str(e)}")
        
        if not file_data:
            raise AgentError("No PDF content provided")

        # Process PDF (assuming process_pdf returns chunks)
        result = await process_pdf(
            file_data=file_data,
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            filename=input_data.get("filename", ""),
            language=str(validate_language(input_data.get("language", "en"))),
            comprehension_level=input_data.get("comprehension_level", "Beginner")
        )

        # Ensure result is dict
        if not isinstance(result, dict):
            result = result.to_dict() if hasattr(result, "to_dict") else {"content": result}

        # Standardize content structure
        content = result.get("content", {})
        if isinstance(content, list):  # If content is just chunks
            content = {"chunks": content}
        content.setdefault("chunks", [])

        # Standardize metadata
        metadata = result.get("metadata", {})
        metadata.update({
            "source_type": "pdf",
            "filename": input_data.get("filename", ""),
            "processing_status": "completed"
        })

        # Build standardized response
        standardized_result = {
            "success": result.get("success", True),
            "content": content,
            "metadata": metadata
        }

        # Add error if present
        if "error" in result:
            standardized_result["error"] = result["error"]

        # Run per-chunk summarization
        await self._process_chunks_summarization(standardized_result["content"])
        return standardized_result

    async def _process_youtube(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process YouTube video content."""
        video_url = str(input_data.get("url") or input_data.get("content", "")).strip()
        if not video_url:
            raise AgentError("No YouTube URL provided")
        
        # Basic URL validation
        if not (video_url.startswith(('http://', 'https://')) or 
                video_url.startswith(('youtube.com/watch', 'youtu.be/'))):
            raise AgentError("Invalid YouTube URL format")

        # Process YouTube video (returns dict from youtube_processor)
        result = await process_youtube(
            url=video_url,
            file_id=input_data.get("file_id", f"temp_{hash(video_url)}"),
            user_id=input_data.get("user_id", "system"),
            filename=input_data.get("filename", ""),
            language=str(validate_language(input_data.get("language", "en")))
        )

        # Ensure result is dict
        if not isinstance(result, dict):
            result = result.to_dict() if hasattr(result, "to_dict") else {"content": result}

        # Initialize content if not present
        content = result.get("content", {})
        
        # Normalize structure: use `chunks` key for consistency with summarization
        if "processed_segments" in content:
            content["chunks"] = content.pop("processed_segments")
        
        # Ensure chunks exist in content
        content.setdefault("chunks", [])

        # Merge/ensure metadata
        metadata = result.get("metadata", {})
        metadata.update({
            "source_type": "youtube",
            "source_url": video_url,
            "processing_status": "completed"
        })

        # Build standardized response
        standardized_result = {
            "success": result.get("success", True),
            "content": content,
            "metadata": metadata
        }

        # Add error if present
        if "error" in result:
            standardized_result["error"] = result["error"]

        # Run per-chunk summarization
        await self._process_chunks_summarization(standardized_result["content"])
        return standardized_result

    async def _process_text(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process plain text content."""
        text = input_data.get("content", "")
        if not text.strip():
            raise AgentError("No text content provided")

        # Process text (assuming process_text returns chunks)
        result = await process_text(
            text=text, 
            chunk_size=self.config.max_chunk_size, 
            chunk_overlap=self.config.chunk_overlap
        )

        # Ensure result is dict
        if not isinstance(result, dict):
            result = result.to_dict() if hasattr(result, "to_dict") else {"content": result}

        # Standardize content structure
        content = result.get("content", {})
        if isinstance(content, list):  # If content is just chunks
            content = {"chunks": content}
        content.setdefault("chunks", [])

        # Standardize metadata
        metadata = result.get("metadata", {})
        metadata.update({
            "source_type": "text",
            "processing_status": "completed"
        })

        # Build standardized response
        standardized_result = {
            "success": result.get("success", True),
            "content": content,
            "metadata": metadata
        }

        # Add error if present
        if "error" in result:
            standardized_result["error"] = result["error"]

        # Run per-chunk summarization
        await self._process_chunks_summarization(standardized_result["content"])
        return standardized_result

    async def _process_url(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process URL content."""
        url = input_data.get("url") or input_data.get("content")
        if not url:
            raise AgentError("No URL provided")

        # Process URL (assuming process_url returns chunks)
        result = await process_url(
            url=url, 
            chunk_size=self.config.max_chunk_size, 
            chunk_overlap=self.config.chunk_overlap
        )

        # Ensure result is dict
        if not isinstance(result, dict):
            result = result.to_dict() if hasattr(result, "to_dict") else {"content": result}

        # Standardize content structure
        content = result.get("content", {})
        if isinstance(content, list):  # If content is just chunks
            content = {"chunks": content}
        content.setdefault("chunks", [])

        # Standardize metadata
        metadata = result.get("metadata", {})
        metadata.update({
            "source_type": "url",
            "source_url": url,
            "processing_status": "completed"
        })

        # Build standardized response
        standardized_result = {
            "success": result.get("success", True),
            "content": content,
            "metadata": metadata
        }

        # Add error if present
        if "error" in result:
            standardized_result["error"] = result["error"]

        # Run per-chunk summarization
        await self._process_chunks_summarization(standardized_result["content"])
        return standardized_result

    def _validate_chunk_structure(self, chunk: Dict[str, Any]) -> bool:
        """Validate that a chunk has the required structure and data types."""
        if not isinstance(chunk, dict):
            return False
            
        # Required fields
        if not isinstance(chunk.get("text", ""), str):
            return False
            
        # Optional but common fields
        if "metadata" in chunk and not isinstance(chunk["metadata"], dict):
            return False
            
        return True

    async def _process_chunks_summarization(self, content_data: Dict[str, Any]) -> None:
        """
        Process chunks in parallel with progress tracking.
        
        Args:
            content_data: Dictionary containing a 'chunks' key with a list of chunk dictionaries.
                         Each chunk should have a 'text' key with string content.
                         
        Raises:
            AgentError: If the input structure is invalid
        """
        if not isinstance(content_data, dict) or "chunks" not in content_data:
            raise AgentError("Input must be a dictionary with a 'chunks' key")
            
        chunks = content_data.get("chunks", [])
        if not isinstance(chunks, list):
            raise AgentError("'chunks' must be a list")
            
        if not chunks:
            logger.warning("No chunks to process")
            return
            
        # Get metadata from parent if available
        metadata = content_data.get("metadata", {})
        
        # Validate all chunks before processing
        for i, chunk in enumerate(chunks):
            if not self._validate_chunk_structure(chunk):
                raise AgentError(f"Invalid chunk structure at index {i}")
            
        try:
            # Generate embeddings first (already batched)
            await self._generate_chunk_embeddings(chunks)
            
            # Process chunks in parallel with semaphore to limit concurrency
            semaphore = asyncio.Semaphore(10)  # Adjust based on rate limits
            
            async def process_single_chunk(chunk: Dict[str, Any], chunk_idx: int) -> Dict[str, Any]:
                async with semaphore:
                    content = str(chunk.get("text", "")).strip()
                    if not content:
                        logger.warning(f"Empty content in chunk {chunk_idx + 1}")
                        return {}
                        
                    try:
                        # Process summary and key concepts
                        summary_data = await self.summarization_agent.process({
                            "content": content,
                            "chunk_id": str(chunk_idx + 1),
                            "total_chunks": str(len(chunks)),
                            "source_type": metadata.get("source_type", "unknown"),
                            "language": metadata.get("language", "en"),
                            "comprehension_level": metadata.get("comprehension_level", "Beginner")
                        })
                        
                        if not isinstance(summary_data, dict):
                            logger.error(f"Unexpected summary data format for chunk {chunk_idx + 1}")
                            return {}
                            
                        # Process the summary data if available
                        if isinstance(summary_data, dict):
                            chunk["metadata"] = {
                                **chunk.get("metadata", {}),
                                "summary": summary_data.get("summary", ""),
                                "key_concepts": summary_data.get("key_concepts", []),
                                "processing_status": "completed"
                            }
                    except Exception as e:
                        logger.error(f"Error processing chunk {chunk_idx + 1}: {e}", exc_info=True)
                        # Mark failed chunks but continue processing others
                        chunk["metadata"] = {
                            **chunk.get("metadata", {}),
                            "processing_status": "failed",
                            "error": str(e)
                        }
                
                # Get embeddings for the current batch
                try:
                    texts = [chunk["text"] for chunk in batch if "text" in chunk]
                    if texts:
                        embeddings = await embedding_service.get_embeddings(
                            texts=texts,
                            provider=self.config.embedding_provider
                        )
                        # Update chunks with embeddings
                        for i, chunk in enumerate(batch):
                            if i < len(embeddings):
                                chunk["embedding"] = embeddings[i]
                except Exception as e:
                    logger.error(f"Error generating embeddings for batch: {e}")
                    # Continue with next batch even if one fails
                    pass
                    
        except Exception as e:
            logger.error(f"Unexpected error in batch processing: {e}")
            raise

        # Generate top-level summary if requested
        if content_data.get("generate_summary", True):
            try:
                summary_result = await self.summarization_agent.process({
                    "content": [chunk.get("text", "") for chunk in chunks if chunk.get("text")],
                    "language": metadata.get("language", "en"),
                    "comprehension_level": metadata.get("comprehension_level", "intermediate")
                })
                if isinstance(summary_result, dict):
                    content_data["metadata"]["summary"] = summary_result.get("summary", "")
                    content_data["metadata"]["key_concepts"] = summary_result.get("key_concepts", [])
            except Exception as e:
                logger.error(f"Error generating top-level summary: {e}", exc_info=True)
                # Don't fail the whole process if top-level summary fails
                content_data["metadata"]["summary_error"] = str(e)

    async def process(
        self, 
        input_data: Dict[str, Any], 
        db: Optional[Any] = None,  # Kept for backward compatibility
        progress_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    ) -> Dict[str, Any]:
        """Process input data based on source type."""
        try:
            await self.validate_input(input_data)
            source_type = input_data.get("source_type")
            if source_type not in self._processors:
                raise AgentError(f"No processor for source type {source_type}")
            
            processor = self._processors[source_type]
            result = await processor(input_data)
            
            # Update file status if file_id is provided
            file_id = input_data.get("file_id")
            if file_id:
                try:
                    repo_manager = await get_repository_manager()
                    file_repo = await repo_manager.file_repo
                    await file_repo.update(file_id, {
                        'processing_status': 'processed',
                        'processing_completed_at': datetime.utcnow()
                    })
                except Exception as e:
                    logger.error(f"Failed to update file status to processed: {str(e)}")
            
            return {
                "status": "success", 
                "source_type": source_type, 
                "content": result, 
                "file_id": file_id
            }
            
        except Exception as e:
            # Update file status to error if file_id is provided
            file_id = input_data.get("file_id")
            if file_id:
                try:
                    repo_manager = await get_repository_manager()
                    file_repo = await repo_manager.file_repo
                    await file_repo.update(file_id, {
                        'processing_status': 'error',
                        'error_message': str(e),
                        'processing_completed_at': datetime.utcnow()
                    })
                except Exception as update_error:
                    logger.error(f"Failed to update file status to error: {str(update_error)}")
            
            logger.error(f"Error processing {input_data.get('source_type')} content: {e}", exc_info=True)
            raise AgentError(f"Failed to process {input_data.get('source_type')} content: {e}")

    async def validate_input(self, input_data: Dict[str, Any]) -> bool:
        if not any([input_data.get("content"), input_data.get("url"), input_data.get("file_path")]):
            raise AgentError("Either 'content', 'url', or 'file_path' must be provided")
        source_type = input_data.get("source_type")
        if not source_type:
            raise AgentError("'source_type' is required")
        if source_type not in self.config.supported_types:
            raise AgentError(f"Unsupported source type: {source_type}. Supported types: {', '.join(self.config.supported_types)}")
        return True
