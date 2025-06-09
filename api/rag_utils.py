"""
Enhanced RAG utilities for Syntext AI
Implements hybrid search, query expansion, re-ranking, and other advanced RAG features
"""
import logging
from typing import List, Dict, Any, Tuple, Optional

from .rag.pipeline import RAGPipeline
from .rag.factory import RAGFactory

logger = logging.getLogger(__name__)

# Create a single global RAG pipeline for all utility functions to use
rag_pipeline = RAGPipeline(config={
    'search_engine': {'default_alpha': 0.7}
})
logger.info("Initialized RAG pipeline with refactored components")

def process_query(query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
    """
    Process and expand the query to improve retrieval quality.
    
    Args:
        query: The original user query
        conversation_history: Optional conversation history for context
        
    Returns:
        tuple: (rewritten_query, expanded_terms)
    """
    try:
        return rag_pipeline.query_processor.process(query, conversation_history)
    except Exception as e:
        logger.error(f"Error in query processing: {e}")
        return query, []  # Fallback to original query

def hybrid_search(vector_results: List[Dict], keyword_results: List[Dict], 
                 query: str, alpha: float = 0.7) -> List[Dict]:
    """
    Combine vector search and keyword search results using a weighted approach
    
    Args:
        vector_results: Results from vector similarity search
        keyword_results: Results from keyword-based search
        query: The user query
        alpha: Weight parameter for blending (higher means more weight to vector)
        
    Returns:
        list: Combined and reranked results
    """
    try:
        return rag_pipeline.search_engine.search(
            query, vector_results, keyword_results, alpha=alpha
        )
    except Exception as e:
        logger.error(f"Error in hybrid search: {e}")
        # Fallback to just returning vector results
        return vector_results

def cross_encoder_rerank(query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    Re-rank the candidate documents using a more sophisticated approach than the initial retrieval.
    
    Args:
        query: User query
        candidates: List of candidate documents from initial retrieval
        top_k: Number of top results to return
        
    Returns:
        list: Re-ranked list of documents
    """
    try:
        return rag_pipeline.reranker.rerank(query, candidates, top_k)
    except Exception as e:
        logger.error(f"Error in reranking: {e}")
        # Fallback to original ranking
        return candidates[:top_k] if candidates else []

def smart_chunk_selection(chunks: List[Dict], query: str, token_budget: int = 3000) -> List[Dict]:
    """
    Intelligently select chunks to fit within token budget, prioritizing relevance
    
    Args:
        chunks: List of content chunks
        query: User query
        token_budget: Maximum tokens to include
        
    Returns:
        list: Selected chunks that fit within token budget
    """
    try:
        return rag_pipeline.chunk_selector.select(chunks, query, token_budget)
    except Exception as e:
        logger.error(f"Error in smart chunk selection: {e}")
        # Fallback to simple truncation of the list
        if chunks:
            return chunks[:3]  # Return first 3 chunks as fallback
        return []
