"""
Main RAG pipeline implementation - demonstrates how to use the components together.
"""

import logging
from typing import List, Dict, Any, Tuple, Optional

from .interfaces import (
    QueryProcessorInterface, 
    SearchEngineInterface,
    ChunkSelectorInterface
)

from .factory import RAGFactory

logger = logging.getLogger(__name__)


class RAGPipeline:
    """
    Complete RAG pipeline that orchestrates all components.
    """
    
    def __init__(self, 
                 query_processor: Optional[QueryProcessorInterface] = None,
                 search_engine: Optional[SearchEngineInterface] = None,
                 chunk_selector: Optional[ChunkSelectorInterface] = None,
                 config: Dict[str, Any] = None):
        """
        Initialize the RAG pipeline.
        
        Args:
            query_processor: Query processor component
            search_engine: Search engine component
            chunk_selector: Chunk selector component
            config: Configuration dictionary for components
        """
        # Use provided components or create new ones using factory
        factory = RAGFactory(config)
        
        self.query_processor = query_processor or factory.create_query_processor()
        self.search_engine = search_engine or factory.create_search_engine()
        self.chunk_selector = chunk_selector or factory.create_chunk_selector()
        
        logger.info("RAG pipeline initialized")
        
    # There was a `process()` here that ran the whole pipeline end to end. It
    # was never called by anything: both real callers reach in for
    # query_processor and chunk_selector directly and do their own retrieval.
    # It also referenced self.search_engine, a component nothing constructs any
    # more. Once the query processor became async it was calling a coroutine
    # without awaiting it, so the dead code was now dead and wrong. Deleted
    # rather than repaired, because repairing an uncalled method only makes the
    # next reader believe it works.
