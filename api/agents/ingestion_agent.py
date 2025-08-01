"""
Ingestion Agent for processing various content types.

This agent handles the ingestion of different content types (PDF, YouTube, text, URLs)
and converts them into a standardized format for further processing.
"""
import logging
from typing import Dict, Any, Optional, Callable, Awaitable, List, Union

from typing import Dict, Any, Optional, Callable, Awaitable, List, Union
from sqlalchemy.orm import Session

from .base_agent import BaseAgent, AgentConfig, AgentError
from .summarization_agent import SummarizationAgent, SummarizationConfig
from ..processors import (
    process_pdf,
    process_youtube,
    process_text,
    process_url
)
from ..services.embedding_service import embedding_service
from ..services.llm_service import llm_service

logger = logging.getLogger(__name__)

class IngestionConfig(AgentConfig):
    """Configuration for the Ingestion Agent."""
    max_chunk_size: int = 4000
    chunk_overlap: int = 200
    supported_types: list = ["pdf", "youtube", "text", "url"]
    timeout: int = 300  # 5 minutes timeout for ingestion
    generate_embeddings: bool = True  # Whether to generate embeddings during ingestion
    embedding_provider: Optional[str] = None  # Force specific embedding provider (mistral, google)
    embedding_batch_size: int = 10  # Number of chunks to process in each embedding batch

@agent("ingestion", {"max_chunk_size": 4000, "chunk_overlap": 200})
class IngestionAgent(BaseAgent[IngestionConfig]):
    """
    Agent responsible for ingesting content from various sources.
    
    This agent handles different content types and processes them into a
    standardized format for further analysis and processing.
    """
    
    def __init__(self, config: Optional[IngestionConfig] = None):
        super().__init__(config or IngestionConfig())
        self._processors: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "pdf": self._process_pdf,
            "youtube": self._process_youtube,
            "text": self._process_text,
            "url": self._process_url
        }
        # Initialize SummarizationAgent with default config
        self.summarization_agent = SummarizationAgent(SummarizationConfig())
    
    async def process(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Process the input data based on its source type.
        
        Args:
            input_data: Dictionary containing:
                - source_type: Type of the source (pdf, youtube, text, url)
                - content: The content to process
                - file_id: Optional ID of the file being processed (required for database storage)
                - Additional metadata specific to the source type
            db: Optional SQLAlchemy session for database operations
                
        Returns:
            Dictionary containing the processed content and metadata
        """
        source_type = input_data.get("source_type", "").lower()
        file_id = input_data.get("file_id")
        
        if source_type not in self.config.supported_types:
            raise AgentError(
                f"Unsupported source type: {source_type}. "
                f"Supported types: {', '.join(self.config.supported_types)}"
            )
        
        logger.info(f"Processing {source_type} content")
        processor = self._processors.get(source_type)
        if not processor:
            raise AgentError(f"No processor found for source type: {source_type}")
        
        try:
            # Process the content using the appropriate processor
            result = await processor(input_data)
            
            # Generate embeddings if enabled
            if self.config.generate_embeddings and isinstance(result, dict) and 'chunks' in result:
                chunks = result['chunks']
                if chunks and isinstance(chunks, list) and len(chunks) > 0:
                    await self._generate_chunk_embeddings(chunks)
            
            # Process text content with SummarizationAgent if available
            if isinstance(result, dict) and 'text' in result:
                context = {
                    'source_type': source_type,
                    'title': result.get('metadata', {}).get('title', ''),
                    'language': input_data.get('language', 'english'),
                    'comprehension_level': input_data.get('comprehension_level', 'intermediate')
                }
                
                # Prepare input for summarization agent
                summarization_input = {
                    'content': result['text'],
                    'file_id': file_id,
                    'language': context['language'],
                    'comprehension_level': context['comprehension_level']
                }
                
                # Process with SummarizationAgent
                summarization_result = await self.summarization_agent.process(
                    summarization_input,
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
            
        # Process the PDF to extract text and chunk it
        result = await process_pdf(
            content=content,
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap
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
        result = await process_youtube(
            video_url=video_url,
            chunk_size=self.config.max_chunk_size,
            chunk_overlap=self.config.chunk_overlap
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
