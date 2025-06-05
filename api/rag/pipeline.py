"""
Main RAG pipeline implementation - demonstrates how to use the components together.
"""

import logging
from typing import List, Dict, Any, Tuple, Optional

from .interfaces import (
    QueryProcessorInterface, 
    SearchEngineInterface,
    ReRankerInterface,
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
                 reranker: Optional[ReRankerInterface] = None,
                 chunk_selector: Optional[ChunkSelectorInterface] = None,
                 config: Dict[str, Any] = None):
        """
        Initialize the RAG pipeline.
        
        Args:
            query_processor: Query processor component
            search_engine: Search engine component
            reranker: Reranker component
            chunk_selector: Chunk selector component
            config: Configuration dictionary for components
        """
        # Use provided components or create new ones using factory
        factory = RAGFactory(config)
        
        self.query_processor = query_processor or factory.create_query_processor()
        self.search_engine = search_engine or factory.create_search_engine()
        self.reranker = reranker or factory.create_reranker()
        self.chunk_selector = chunk_selector or factory.create_chunk_selector()
        
        logger.info("RAG pipeline initialized")
        
    def process(self, 
                query: str, 
                vector_results: List[Dict] = None,
                keyword_results: List[Dict] = None,
                conversation_history: Optional[str] = None,
                token_budget: int = 3000,
                top_k: int = 5,
                **kwargs) -> Dict[str, Any]:
        """
        Process a query through the complete RAG pipeline.
        
        Args:
            query: User query
            vector_results: Pre-fetched vector search results (optional)
            keyword_results: Pre-fetched keyword search results (optional)
            conversation_history: Optional conversation history for context
            token_budget: Maximum tokens for context selection
            top_k: Number of top results to return after reranking
            **kwargs: Additional configuration parameters
            
        Returns:
            dict: Results from the RAG pipeline including:
                - processed_query: The processed query
                - expanded_terms: Additional search terms
                - search_results: Combined search results
                - reranked_results: Results after reranking
                - selected_chunks: Final chunks selected for the LLM
        """
        results = {}
        
        try:
            # Step 1: Process and expand the query
            processed_query, expanded_terms = self.query_processor.process(
                query, conversation_history=conversation_history
            )
            results['processed_query'] = processed_query
            results['expanded_terms'] = expanded_terms
            
            # Step 2: Hybrid search (if results provided)
            if vector_results is not None and keyword_results is not None:
                search_results = self.search_engine.search(
                    processed_query, vector_results, keyword_results, **kwargs
                )
                results['search_results'] = search_results
            else:
                # No search results provided, we can't continue the pipeline
                logger.warning("No search results provided, cannot continue RAG pipeline")
                return results
            
            # Step 3: Rerank results
            reranked_results = self.reranker.rerank(
                processed_query, search_results, top_k=top_k
            )
            results['reranked_results'] = reranked_results
            
            # Step 4: Select chunks for context
            selected_chunks = self.chunk_selector.select(
                reranked_results, processed_query, token_budget=token_budget
            )
            results['selected_chunks'] = selected_chunks
            
            return results
            
        except Exception as e:
            logger.error(f"Error in RAG pipeline: {e}", exc_info=True)
            # Return whatever results we have so far
            return results
