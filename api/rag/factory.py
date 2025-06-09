"""
Factory for creating and configuring RAG components.
"""

import logging
from typing import Dict, Any, Optional, Type

from .interfaces import (
    QueryProcessorInterface, 
    SearchEngineInterface,
    ReRankerInterface,
    ChunkSelectorInterface
)

from .query_processor import DefaultQueryProcessor
from .search_engine import HybridSearchEngine
from .reranker import CrossEncoderReRanker
from .chunk_selector import SmartChunkSelector

logger = logging.getLogger(__name__)


class RAGFactory:
    """
    Factory for creating and configuring RAG components.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize the RAG factory with optional configuration.
        
        Args:
            config: Configuration dictionary for RAG components
        """
        self.config = config or {}
        
        # Default component classes
        self._query_processor_class = DefaultQueryProcessor
        self._search_engine_class = HybridSearchEngine
        self._reranker_class = CrossEncoderReRanker
        self._chunk_selector_class = SmartChunkSelector
        
    def create_query_processor(self) -> QueryProcessorInterface:
        """
        Create a query processor instance.
        
        Returns:
            QueryProcessorInterface: Configured query processor
        """
        processor_config = self.config.get('query_processor', {})
        return self._query_processor_class(**processor_config)
        
    def create_search_engine(self) -> SearchEngineInterface:
        """
        Create a search engine instance.
        
        Returns:
            SearchEngineInterface: Configured search engine
        """
        engine_config = self.config.get('search_engine', {})
        return self._search_engine_class(**engine_config)
        
    def create_reranker(self) -> ReRankerInterface:
        """
        Create a reranker instance.
        
        Returns:
            ReRankerInterface: Configured reranker
        """
        reranker_config = self.config.get('reranker', {})
        return self._reranker_class(**reranker_config)
        
    def create_chunk_selector(self) -> ChunkSelectorInterface:
        """
        Create a chunk selector instance.
        
        Returns:
            ChunkSelectorInterface: Configured chunk selector
        """
        selector_config = self.config.get('chunk_selector', {})
        return self._chunk_selector_class(**selector_config)
        
    def create_pipeline(self) -> Dict[str, Any]:
        """
        Create a complete RAG pipeline with all components.
        
        Returns:
            dict: Dictionary containing all RAG components
        """
        pipeline = {
            'query_processor': self.create_query_processor(),
            'search_engine': self.create_search_engine(),
            'reranker': self.create_reranker(),
            'chunk_selector': self.create_chunk_selector()
        }
        
        logger.info(f"Created RAG pipeline with components: {', '.join(pipeline.keys())}")
        return pipeline
        
    def set_query_processor_class(self, cls: Type[QueryProcessorInterface]) -> None:
        """Set custom query processor implementation."""
        self._query_processor_class = cls
        
    def set_search_engine_class(self, cls: Type[SearchEngineInterface]) -> None:
        """Set custom search engine implementation."""
        self._search_engine_class = cls
        
    def set_reranker_class(self, cls: Type[ReRankerInterface]) -> None:
        """Set custom reranker implementation."""
        self._reranker_class = cls
        
    def set_chunk_selector_class(self, cls: Type[ChunkSelectorInterface]) -> None:
        """Set custom chunk selector implementation."""
        self._chunk_selector_class = cls
