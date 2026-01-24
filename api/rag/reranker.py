"""
Re-ranking components for RAG systems.
"""

import logging
from typing import List, Dict, Any

from ..llm_service import get_text_embedding
from .interfaces import ReRankerInterface

logger = logging.getLogger(__name__)


class CrossEncoderReRanker(ReRankerInterface):
    """
    Cross-encoder based re-ranker for more accurate document ranking.
    """
    
    def rerank(self, query: str, candidates: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Re-rank the candidate documents using a more sophisticated approach than the initial retrieval.
        
        Args:
            query: User query
            candidates: List of candidate documents from initial retrieval
            top_k: Number of top results to return
            
        Returns:
            list: Re-ranked list of documents
        """
        if not candidates:
            return []
            
        try:
            # Extract content for each candidate
            candidate_contents = []
            for candidate in candidates:
                content = candidate.get('content', '')
                file_name = candidate.get('file_name') or ''
                page_number = candidate.get('page_number')
                meta = candidate.get('meta_data') or {}

                prefix_parts = []
                if file_name:
                    prefix_parts.append(f"file={file_name}")
                if page_number is not None:
                    prefix_parts.append(f"page={page_number}")
                if meta.get('type') == 'video':
                    start_time = meta.get('start_time')
                    end_time = meta.get('end_time')
                    if start_time is not None:
                        prefix_parts.append(f"t={start_time}")
                    if end_time is not None:
                        prefix_parts.append(f"t_end={end_time}")

                prefix = " ".join(prefix_parts)
                content_text = f"{prefix}\n{content}" if prefix else content
                candidate_contents.append(content_text)
                
            # Generate query-document pairs for scoring
            query_doc_pairs = []
            for content in candidate_contents:
                # Truncate content if needed to avoid token limits
                truncated_content = content[:1000]  # Limit content to 1000 chars
                query_doc_pairs.append(f"Query: {query} Document: {truncated_content}")
                
            # Get similarity scores from cross-encoder
            try:
                # In a real cross-encoder implementation, we'd use a model like:
                # scores = cross_encoder.predict(query_doc_pairs)
                # For now, use a simpler embedding-based approach
                query_embedding = get_text_embedding(query)
                
                scores = []
                for content in candidate_contents:
                    doc_embedding = get_text_embedding(content[:600])
                    # Calculate cosine similarity (simplified)
                    similarity = self._compute_similarity(query_embedding, doc_embedding)
                    scores.append(similarity)
            except Exception as e:
                logger.error(f"Error calculating reranking scores: {e}", exc_info=True)
                # Fallback to original ranking if scoring fails
                return candidates[:top_k]
                
            # Create index-score pairs and sort by score
            scored_indices = list(enumerate(scores))
            scored_indices.sort(key=lambda x: x[1], reverse=True)
            
            # Reorder candidates based on new ranking and add score
            reranked_candidates = []
            for i, score in scored_indices[:top_k]:
                candidate = candidates[i].copy()
                candidate['rerank_score'] = score
                reranked_candidates.append(candidate)
            
            return reranked_candidates
            
        except Exception as e:
            logger.error(f"Error in reranking: {e}", exc_info=True)
            # Fallback to original ranking
            return candidates[:top_k]
            
    def _compute_similarity(self, vec1, vec2):
        """
        Compute similarity between two vectors.
        """
        try:
            # Try using sklearn's cosine_similarity
            from sklearn.metrics.pairwise import cosine_similarity
            return cosine_similarity([vec1], [vec2])[0][0]
        except ImportError:
            # Fallback to a simple dot product normalization
            import math
            
            # Calculate dot product
            dot_product = sum(a * b for a, b in zip(vec1, vec2))
            
            # Calculate magnitudes
            magnitude1 = math.sqrt(sum(a * a for a in vec1))
            magnitude2 = math.sqrt(sum(b * b for b in vec2))
            
            # Calculate cosine similarity
            if magnitude1 > 0 and magnitude2 > 0:
                return dot_product / (magnitude1 * magnitude2)
            else:
                return 0.0
