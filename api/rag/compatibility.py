"""
Backward compatibility module for transitioning from old rag_utils.py functions.
"""

import logging
from typing import List, Dict, Any, Tuple, Optional

from .pipeline import RAGPipeline
from .factory import RAGFactory

logger = logging.getLogger(__name__)


# Create a global pipeline instance for compatibility functions
_rag_pipeline = RAGPipeline(config={
    'search_engine': {'default_alpha': 0.7}
})


def process_query(query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
    """
    Legacy compatibility function for process_query from rag_utils.py
    
    Args:
        query: The original user query
        conversation_history: Optional conversation history for context
        
    Returns:
        tuple: (rewritten_query, expanded_terms)
    """
    return _rag_pipeline.query_processor.process(query, conversation_history)


def hybrid_search(vector_results: List[Dict], keyword_results: List[Dict], 
                 query: str, alpha: float = 0.7) -> List[Dict]:
    """
    Legacy compatibility function for hybrid_search from rag_utils.py
    
    Args:
        vector_results: Results from vector similarity search
        keyword_results: Results from keyword-based search
        query: The user query
        alpha: Weight parameter for blending (higher means more weight to vector)
        
    Returns:
        list: Combined and ranked results
    """
    return _rag_pipeline.search_engine.search(
        query, vector_results, keyword_results, alpha=alpha
    )


def cross_encoder_rerank(query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    Legacy compatibility function for cross_encoder_rerank from rag_utils.py
    
    Args:
        query: User query
        candidates: List of candidate documents from initial retrieval
        top_k: Number of top results to return
        
    Returns:
        list: Re-ranked list of documents
    """
    return _rag_pipeline.reranker.rerank(query, candidates, top_k)


def smart_chunk_selection(chunks: List[Dict], query: str, token_budget: int = 3000) -> List[Dict]:
    """
    Legacy compatibility function for smart_chunk_selection from rag_utils.py
    
    Args:
        chunks: List of content chunks
        query: User query
        token_budget: Maximum tokens to include
        
    Returns:
        list: Selected chunks that fit within token budget
    """
    return _rag_pipeline.chunk_selector.select(chunks, query, token_budget)
