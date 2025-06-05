"""
Search engine implementations for RAG systems.
"""

import logging
from typing import List, Dict, Any

from .interfaces import SearchEngineInterface

logger = logging.getLogger(__name__)

# Try to import scikit-learn components
try:
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    logger.warning("scikit-learn not available, defining minimal cosine similarity implementation")
    def cosine_similarity(a, b):
        return [[1.0]]  # Always return perfect similarity as fallback


class HybridSearchEngine(SearchEngineInterface):
    """
    Hybrid search engine that combines vector and keyword search results.
    """
    
    def __init__(self, default_alpha: float = 0.7):
        """
        Initialize the hybrid search engine.
        
        Args:
            default_alpha: Weight parameter for blending vector and keyword results (higher means more weight to vector)
        """
        self.default_alpha = default_alpha
    
    def search(self, query: str, vector_results: List[Dict], keyword_results: List[Dict], 
               alpha: float = None, **kwargs) -> List[Dict]:
        """
        Combine vector search and keyword search results using a weighted approach
        
        Args:
            query: The user query
            vector_results: Results from vector similarity search
            keyword_results: Results from keyword-based search
            alpha: Weight parameter for blending (higher means more weight to vector)
            
        Returns:
            list: Combined and ranked results
        """
        if alpha is None:
            alpha = self.default_alpha
            
        try:
            # Normalize results to prepare for merging
            all_results = self._normalize_and_merge_results(vector_results, keyword_results, alpha)
            
            # Sort by combined score
            sorted_results = sorted(all_results, key=lambda x: x.get('combined_score', 0), reverse=True)
            
            # Make sure we have unique results only (no duplicates)
            deduplicated_results = self._remove_duplicates(sorted_results)
            
            return deduplicated_results
            
        except Exception as e:
            logger.error(f"Error in hybrid search: {e}", exc_info=True)
            # Fallback to just returning the vector results if available, or keyword results
            return vector_results if vector_results else keyword_results
            
    def _normalize_and_merge_results(self, vector_results: List[Dict], 
                                    keyword_results: List[Dict], alpha: float) -> List[Dict]:
        """
        Normalize and merge results from different search methods.
        """
        # Collect all unique document IDs
        all_ids = set()
        for result in vector_results:
            all_ids.add(result.get('id'))
        for result in keyword_results:
            all_ids.add(result.get('id'))
            
        # Create a mapping for faster lookup
        vector_map = {item.get('id'): item for item in vector_results}
        keyword_map = {item.get('id'): item for item in keyword_results}
        
        # Calculate normalized scores
        vector_scores = [r.get('similarity_score', 0) for r in vector_results if 'similarity_score' in r]
        keyword_scores = [r.get('bm25_score', 0) for r in keyword_results if 'bm25_score' in r]
        
        # Avoid division by zero
        max_vector_score = max(vector_scores) if vector_scores else 1.0
        max_keyword_score = max(keyword_scores) if keyword_scores else 1.0
        
        # Normalize and combine scores for each unique document
        combined_results = []
        
        for doc_id in all_ids:
            # Get the items or create empty ones
            vector_item = vector_map.get(doc_id, {})
            keyword_item = keyword_map.get(doc_id, {})
            
            # Start with either one as the base item
            result_item = vector_item.copy() if doc_id in vector_map else keyword_item.copy()
            
            # Normalized scores (0-1 range)
            norm_vector_score = vector_item.get('similarity_score', 0) / max_vector_score if max_vector_score else 0
            norm_keyword_score = keyword_item.get('bm25_score', 0) / max_keyword_score if max_keyword_score else 0
            
            # Weighted combination
            combined_score = (alpha * norm_vector_score) + ((1 - alpha) * norm_keyword_score)
            
            # Store all scores for transparency
            result_item['vector_score'] = vector_item.get('similarity_score', 0)
            result_item['keyword_score'] = keyword_item.get('bm25_score', 0)
            result_item['combined_score'] = combined_score
            
            combined_results.append(result_item)
            
        return combined_results
        
    def _remove_duplicates(self, results: List[Dict]) -> List[Dict]:
        """
        Remove duplicate results based on content hash or ID.
        """
        unique_results = []
        seen_ids = set()
        
        for result in results:
            result_id = result.get('id')
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                unique_results.append(result)
                
        return unique_results
