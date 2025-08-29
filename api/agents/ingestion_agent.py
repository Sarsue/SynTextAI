"""
Ingestion Agent for processing and normalizing various content types.

This module provides the IngestionAgent class which is responsible for ingesting content
from various sources (PDFs, YouTube videos, text, and URLs) and converting them into a
standardized format suitable for further processing by other agents in the SynTextAI system.
"""
import logging
from typing import Dict, Any, Optional, Callable, Awaitable, List, Union
from sqlalchemy.orm import Session

from api.agents.base_agent import BaseAgent, AgentConfig, AgentError
from api.agents.agent_factory import agent
from api.agents.summarization_agent import SummarizationAgent, SummarizationConfig
from api.utils.language_utils import validate_language
from api.processors import (
    process_pdf,
    process_youtube,
    process_text,
    process_url
)
from api.services.embedding_service import embedding_service
from api.services.llm_service import llm_service

logger = logging.getLogger(__name__)

class IngestionConfig(AgentConfig):
    """
    Configuration for the Ingestion Agent.
    
    This configuration class defines parameters that control how the IngestionAgent
    processes different types of content.
    
    Attributes:
        max_chunk_size: Maximum size (in characters) for each content chunk.
                        Default: 4000
        chunk_overlap: Number of characters to overlap between chunks.
                      Default: 200
        supported_types: List of supported content types.
                       Default: ["pdf", "youtube", "text", "url"]
        timeout: Maximum processing time in seconds before timing out.
                Default: 300 (5 minutes)
        generate_embeddings: Whether to generate embeddings during ingestion.
                           Default: True
        embedding_provider: Specific embedding provider to use ('mistral', 'google').
                          If None, uses the default provider.
                          Default: None
        embedding_batch_size: Number of chunks to process in each embedding batch.
                            Default: 10
    """
    max_chunk_size: int = 4000
    chunk_overlap: int = 200
    supported_types: list = ["pdf", "youtube", "text", "url"]
    timeout: int = 300  # 5 minutes timeout for ingestion
    generate_embeddings: bool = True
    embedding_provider: Optional[str] = None
    embedding_batch_size: int = 10

@agent(
    name="ingestion",
    description="Handles content ingestion from various sources including PDFs, YouTube, text, and URLs",
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
    """
    Agent responsible for ingesting and normalizing content from various sources.
    
    The IngestionAgent serves as the entry point for all content processing in SynTextAI.
    It handles different content types (PDFs, YouTube videos, text, and URLs) and
    converts them into a standardized format suitable for further processing by
    specialized agents (e.g., SummarizationAgent, QuizAgent).
    
    Key Features:
    - Content type detection and routing
    - Document chunking with configurable size and overlap
    - Metadata extraction and normalization
    - Optional embedding generation
    - Integration with external services (YouTube API, web scrapers, etc.)
    - Progress tracking and error handling
    
    The agent maintains a registry of processor functions for different content types
    and automatically selects the appropriate one based on the input source_type.
    
    Example:
        ```python
        # Initialize with custom configuration
        config = IngestionConfig(
            max_chunk_size=3000,
            chunk_overlap=300,
            generate_embeddings=True
        )
        agent = IngestionAgent(config)
        
        # Process content
        result = await agent.process({
            "source_type": "pdf",
            "file_path": "document.pdf",
            "file_id": "doc-123",
            "metadata": {"title": "Sample Document"}
        })
        ```
    """
    
    def __init__(self, config: Optional[Union[Dict[str, Any], AgentConfig, IngestionConfig]] = None):
        # Initialize with default config if none provided
        super().__init__(config or IngestionConfig())
        
        # Convert AgentConfig to IngestionConfig if needed
        if isinstance(self.config, AgentConfig) and not isinstance(self.config, IngestionConfig):
            # Convert AgentConfig to dict and then to IngestionConfig
            config_dict = self.config.dict()
            self.config = IngestionConfig(**config_dict)
        # Ensure we have an IngestionConfig instance
        elif not isinstance(self.config, IngestionConfig):
            self.config = IngestionConfig()
            
        self._processors: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "pdf": self._process_pdf,
            "youtube": self._process_youtube,
            "text": self._process_text,
            "url": self._process_url
        }
        # Initialize SummarizationAgent with default config
        self.summarization_agent = SummarizationAgent(SummarizationConfig())
    
    async def _process_pdf_new(self, file_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        New implementation of PDF processing with explicit file path.
        
        Args:
            file_path: Path to the PDF file
            metadata: Dictionary containing metadata about the PDF
                
        Returns:
            Dictionary containing processed content with chunks and metadata
        """
        try:
            # Read the file content
            with open(file_path, 'rb') as f:
                file_data = f.read()
                
            # Call process_pdf with all required parameters
            result = await process_pdf(
                file_data=file_data,
                file_id=0,  # Will be set by the caller
                user_id='system',  # Will be set by the caller
                filename=os.path.basename(file_path),
                chunk_size=self.config.max_chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                language=metadata.get('language', 'English'),
                comprehension_level=metadata.get('comprehension_level', 'Beginner')
            )
            
            # Ensure metadata is properly set
            if 'metadata' not in result:
                result['metadata'] = {}
                
            result['metadata'].update({
                'source_type': 'pdf',
                'original_filename': metadata.get('filename', 'document.pdf')
            })
            
            return result
            
        except Exception as e:
            logger.error(f"Error in _process_pdf_new: {str(e)}")
            raise AgentError(f"Failed to process PDF: {str(e)}")

    async def _process_youtube_new(self, video_url: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process YouTube video with optional audio file for transcription.
        
        Args:
            video_url: URL of the YouTube video (can be full URL or just video ID)
            metadata: Dictionary containing metadata about the video, may include:
                - audio_file: Path to pre-downloaded audio file (optional)
                - language: Language code for transcription (default: 'en')
                - title: Video title (optional)
                - description: Video description (optional)
                
        Returns:
            Dictionary containing:
                - chunks: List of content chunks with text and metadata
                - metadata: Additional video metadata
                
        Raises:
            AgentError: If processing fails
        """
        try:
            # Extract video ID if full URL is provided
            if 'youtube.com' in video_url or 'youtu.be' in video_url:
                from urllib.parse import urlparse, parse_qs
                
                # Handle youtu.be short URLs
                if 'youtu.be' in video_url:
                    video_id = urlparse(video_url).path.lstrip('/')
                else:
                    # Handle full youtube.com URLs
                    parsed = urlparse(video_url)
                    if 'v' in parse_qs(parsed.query):
                        video_id = parse_qs(parsed.query)['v'][0]
                    else:
                        video_id = parsed.path.split('/')[-1]
            else:
                video_id = video_url
                video_url = f'https://www.youtube.com/watch?v={video_id}'
            
            # Prepare metadata
            if 'metadata' not in metadata:
                metadata['metadata'] = {}
                
            metadata['metadata'].update({
                'source_type': 'youtube',
                'source_url': video_url,
                'video_id': video_id,
                'language': metadata.get('language', 'en')
            })
            
            # Process with audio file if provided
            audio_file = metadata.pop('audio_file', None)
            if audio_file and os.path.exists(audio_file):
                try:
                    # Process audio file directly
                    from ..processors.youtube_processor import YouTubeProcessor
                    processor = YouTubeProcessor()
                    
                    # Get and validate language from metadata or default to English
                    language = validate_language(metadata.get('language', 'en'))
                    
                    # Transcribe audio
                    segments = await processor._transcribe_with_whisper(
                        video_id=video_id,
                        target_lang_code=language
                    )
                    
                    if not segments:
                        raise AgentError("No transcription segments returned")
                    
                    # Format result
                    result = {
                        'chunks': segments,
                        'metadata': metadata['metadata']
                    }
                    
                    return result
                    
                except Exception as e:
                    logger.error(f"Error processing audio file {audio_file}: {str(e)}", exc_info=True)
                    # Fall through to standard processing if audio processing fails
            
            # Fall back to standard YouTube processing without audio file
            result = await process_youtube(
                video_url=video_url,
                file_id=metadata.get('file_id'),
                user_id=metadata.get('user_id'),
                chunk_size=self.config.max_chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                language=validate_language(metadata.get('language', 'en'))
            )
            
            # Ensure metadata is properly set
            if 'metadata' not in result:
                result['metadata'] = {}
                
            result['metadata'].update(metadata['metadata'])
            
            return result
            
        except Exception as e:
            logger.error(f"Error in _process_youtube_new: {str(e)}", exc_info=True)
            raise AgentError(f"Failed to process YouTube video {video_url}: {str(e)}")

    async def process(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Process the input data based on source type.
        
        Supports both old-style (dictionary input) and new-style (explicit args) calls.
        
        Args:
            input_data: Dictionary containing:
                - source_type: Type of the source (pdf, youtube, text, url)
                - content: The content to process (for old style)
                - file_path: Path to the file (for new style PDF processing)
                - url: YouTube URL (for new style YouTube processing)
                - file_id: Optional ID of the file being processed
                - metadata: Optional dictionary of metadata
            db: Optional SQLAlchemy session for database operations
                
        Returns:
            Dictionary containing the processed content and metadata
        """
        try:
            # Ensure config is properly initialized
            if not hasattr(self.config, 'supported_types') or not self.config.supported_types:
                self.logger.warning("Config not properly initialized, using default values")
                self.config = IngestionConfig()
                
            source_type = input_data.get("source_type")
            file_id = input_data.get("file_id")
            
            if not source_type:
                raise AgentError("No source_type specified in input data")
                
            self.logger.debug(f"Processing source type: {source_type}")
            self.logger.debug(f"Available processors: {list(self._processors.keys())}")
            
            # Handle new-style calls first
            if source_type == "pdf" and "file_path" in input_data:
                self.logger.debug("Using new-style PDF processor")
                result = await self._process_pdf_new(
                    file_path=input_data["file_path"],
                    metadata=input_data.get("metadata", {})
                )
            elif source_type == "youtube" and "url" in input_data:
                self.logger.debug("Using new-style YouTube processor")
                result = await self._process_youtube_new(
                    video_url=input_data["url"],
                    metadata=input_data.get("metadata", {})
                )
            else:
                # Fall back to original processor for other cases
                if source_type not in self.config.supported_types:
                    raise AgentError(f"Unsupported source type: {source_type}. Supported types: {self.config.supported_types}")
                
                if source_type not in self._processors:
                    raise AgentError(f"No processor available for source type: {source_type}")
                    
                processor = self._processors[source_type]
                result = await processor(input_data)
            
            # Generate embeddings if enabled
            if hasattr(self.config, 'generate_embeddings') and self.config.generate_embeddings and "chunks" in result:
                await self._generate_chunk_embeddings(result["chunks"])
            
            # Generate summary and key concepts if we have content
            content = input_data.get("content") or input_data.get("file_path") or input_data.get("url")
            if content:
                # Prepare summarization input with required fields
                summarization_input = {
                    "content": content,
                    "source_type": source_type,
                    "language": input_data.get("language", "english"),
                    "comprehension_level": input_data.get("comprehension_level", "intermediate"),
                    "metadata": input_data.get("metadata", {})
                }
                
                summarization_result = await self.summarization_agent.process(
                    input_data=summarization_input,
                    db=db
                )
                
                # Update result with summary and key concepts
                if 'metadata' not in result:
                    result['metadata'] = {}
                    
                if summarization_result.get('summary'):
                    result['metadata']['summary'] = summarization_result['summary']
                    
                if summarization_result.get('key_concepts'):
                    result['metadata']['key_concepts'] = summarization_result['key_concepts']
            
            return {
                "status": "success",
                "source_type": source_type,
                "content": result,
                "file_id": file_id
            }
            
        except Exception as e:
            logger.error(f"Error processing {source_type} content: {str(e)}", exc_info=True)
            raise AgentError(f"Failed to process {source_type} content: {str(e)}")
    
    async def _process_pdf(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process PDF content.
        
        Args:
            input_data: Dictionary containing:
                - content: Path to PDF file or binary content
                - metadata: Optional metadata about the PDF
                
        Returns:
            Dictionary containing processed content with chunks and metadata
        """
        content = input_data.get("content")
        if not content:
            raise AgentError("No content provided for PDF processing")
            
        # Import the PDF processor
        from api.processors.pdf_processor import process_pdf as pdf_processor
        
        # Process the PDF to extract text and chunk it
        result = await pdf_processor(
            file_data=content,
            file_id=input_data.get('file_id', 'temp_' + str(hash(str(content[:1000]))) if content else 'unknown'),
            user_id=input_data.get('user_id', 'system'),
            filename=input_data.get('filename', 'document.pdf'),
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            language=input_data.get('language', 'en')
        )
        
        # Add source metadata if not present
        if 'metadata' not in result:
            result['metadata'] = {}
            
        result['metadata'].update({
            'source_type': 'pdf',
            'original_filename': input_data.get('filename', 'document.pdf')
        })
        
        return result
    
    async def _process_youtube(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process YouTube video content.
        
        Args:
            input_data: Dictionary containing:
                - url or content: YouTube URL to process
                - metadata: Optional metadata about the video
                
        Returns:
            Dictionary containing processed content with chunks and metadata
        """
        video_url = input_data.get("url") or input_data.get("content")
        if not video_url:
            raise AgentError("No YouTube URL provided")
            
        # Process the YouTube video to get transcript and chunk it
        from api.processors.youtube_processor import process_youtube as youtube_processor
        
        # Get file_id from input_data or generate a temporary one
        file_id = input_data.get('file_id', 'temp_' + str(hash(video_url)))
        
        # Call the processor with required parameters
        # The process_youtube function expects (video_url, file_id, user_id, **kwargs)
        # and will handle passing these to the processor's process method
        result = await youtube_processor(
            video_url=video_url,
            file_id=file_id,
            user_id=input_data.get('user_id', 'system'),
            # Don't pass filename here as it's already handled in process_youtube
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            language=input_data.get('metadata', {}).get('language', 'English'),
            comprehension_level=input_data.get('metadata', {}).get('comprehension_level', 'Beginner')
        )
        
        # Add source metadata if not present
        if 'metadata' not in result:
            result['metadata'] = {}
            
        result['metadata'].update({
            'source_type': 'youtube',
            'source_url': video_url
        })
        
        return result
    
    async def _process_text(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process plain text content."""
        text = input_data.get("content", "")
        if not text.strip():
            raise AgentError("No text content provided")
            
        return await process_text(
            text=text,
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap
        )
    
    async def _process_url(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process web URL content."""
        url = input_data.get("url") or input_data.get("content")
        if not url:
            raise AgentError("No URL provided")
            
        return await process_url(
            url=url,
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap
        )
    
    async def _generate_chunk_embeddings(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Generate embeddings for text chunks in batches.
        
        Args:
            chunks: List of chunk dictionaries, each containing at least a 'text' key
            
        Modifies the chunks in place by adding an 'embedding' key to each chunk.
        """
        if not chunks or not self.config.generate_embeddings:
            return
            
        try:
            # Extract texts for embedding
            texts = [chunk.get('text', '') for chunk in chunks]
            if not any(texts):
                logger.warning("No valid text found in chunks for embedding")
                return
                
            # Generate embeddings in batches
            logger.info(f"Generating embeddings for {len(texts)} chunks...")
            embeddings = await embedding_service.get_embeddings(
                texts=texts,
                provider=self.config.embedding_provider
            )
            
            # Attach embeddings to chunks
            for i, chunk in enumerate(chunks):
                if i < len(embeddings) and embeddings[i]:
                    chunk['embedding'] = embeddings[i]
                    
            logger.info(f"Generated embeddings for {len(embeddings)} chunks")
            
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            # Don't fail the entire process if embeddings fail
            if self.config.embedding_provider:
                logger.warning("Falling back to no embeddings due to error")
    
    async def _generate_summary(self, text: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate a summary of the given text using the LLM service.
        
        Args:
            text: The text to summarize
            context: Optional context information
            
        Returns:
            Generated summary text
        """
        if not text.strip():
            return ""
            
        try:
            prompt = f"""
            Please provide a concise summary of the following text. 
            Focus on the key points and main ideas.
            
            Text:
            {text}
            
            Summary:
            """
            
            # Add context if provided
            if context:
                context_str = "\n".join(f"{k}: {v}" for k, v in context.items())
                prompt = f"Context:\n{context_str}\n\n{prompt}"
            
            summary = await llm_service.generate_text(
                prompt=prompt,
                temperature=0.3,  # Lower temperature for more focused summaries
                max_tokens=500
            )
            
            return summary.strip()
            
        except Exception as e:
            logger.error(f"Error generating summary: {e}", exc_info=True)
            raise AgentError(f"Failed to generate summary: {str(e)}")
    

            
    async def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate the input data before processing."""
        if not input_data.get("content") and not input_data.get("url"):
            raise AgentError("Either 'content' or 'url' must be provided")
            
        source_type = input_data.get("source_type")
        if not source_type:
            raise AgentError("'source_type' is required")
            
        if source_type not in self.config.supported_types:
            raise AgentError(
                f"Unsupported source type: {source_type}. "
                f"Supported types: {', '.join(self.config.supported_types)}"
            )
            
        return True
