"""
Interface definitions for RAG components.
These abstract base classes define the contract that concrete implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple, Optional


class QueryProcessorInterface(ABC):
    """Interface for query processing components."""
    
    @abstractmethod
    def process(self, query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
        """
        Process a query to improve retrieval quality.
        
        Args:
            query: The original user query
            conversation_history: Optional conversation history for context
            
        Returns:
            tuple: (processed_query, expanded_terms)
        """
        pass


class SearchEngineInterface(ABC):
    """Interface for search engine components."""
    
    @abstractmethod
    def search(self, query: str, vector_results: List[Dict], keyword_results: List[Dict], **kwargs) -> List[Dict]:
        """
        Perform search and return ranked results.
        
        Args:
            query: The user query
            vector_results: Results from vector similarity search
            keyword_results: Results from keyword-based search
            
        Returns:
            list: Combined and ranked search results
        """
        pass


class ReRankerInterface(ABC):
    """Interface for re-ranking components."""
    
    @abstractmethod
    def rerank(self, query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Re-rank candidate documents based on relevance to query.
        
        Args:
            query: User query
            candidates: List of candidate documents from initial retrieval
            top_k: Number of top results to return
            
        Returns:
            list: Re-ranked list of documents
        """
        pass


class ChunkSelectorInterface(ABC):
    """Interface for chunk selection components."""
    
    @abstractmethod
    def select(self, chunks: List[Dict], query: str, token_budget: int = 3000) -> List[Dict]:
        """
        Select chunks to fit within token budget, optimizing for relevance.
        
        Args:
            chunks: List of content chunks
            query: User query
            token_budget: Maximum tokens to include
            
        Returns:
            list: Selected chunks that fit within token budget
        """
        pass
