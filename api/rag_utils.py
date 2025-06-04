"""
Enhanced RAG utilities for Syntext AI
Implements hybrid search, query expansion, re-ranking, and other advanced RAG features
"""
import logging
import re
from typing import List, Dict, Any, Tuple, Optional

# Fault-tolerant imports
try:
    import numpy as np
except ImportError:
    import array as np
    logging.warning("NumPy not available, using alternative array implementation")

# Try to import LLM service functions
try:
    from llm_service import get_text_embedding, prompt_llm
except ImportError:
    logging.warning("Could not import from llm_service, defining fallback functions")
    def get_text_embedding(text):
        logging.error("llm_service.get_text_embedding not available")
        return [0.0] * 768  # Return zero vector of typical embedding size
        
    def prompt_llm(text):
        logging.error("llm_service.prompt_llm not available")
        return "LLM service unavailable. Please check dependencies."

# Try to import scikit-learn components
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    logging.warning("scikit-learn not available, defining minimal TF-IDF implementation")
    # Simple fallback implementation
    class TfidfVectorizer:
        def __init__(self):
            pass
            
        def fit_transform(self, texts):
            return [[1.0] for _ in texts]  # Simple dummy vectors
            
        def transform(self, texts):
            return [[1.0] for _ in texts]
    
    def cosine_similarity(a, b):
        return [[1.0]]  # Always return perfect similarity as fallback

logger = logging.getLogger(__name__)

def process_query(query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
    """
    Process and expand the query to improve retrieval quality.
    
    Args:
        query: The original user query
        conversation_history: Optional conversation history for context
        
    Returns:
        tuple: (rewritten_query, expanded_terms)
    """
    # For complex queries, consider decomposition
    try:
        # Simple query expansion with synonyms and related terms
        if len(query) > 10:  # Only do expansion for non-trivial queries
            expansion_prompt = f"""
            For the following question, provide 3-5 additional relevant search terms or phrases that would help retrieve relevant information.
            Format the output as a comma-separated list of individual terms or short phrases.
            
            Original query: {query}
            
            Related search terms:"""
            
            expanded_terms_text = prompt_llm(expansion_prompt)
            expanded_terms = [term.strip() for term in expanded_terms_text.split(',') if term.strip()]
            
            # For complex queries, try query reformulation for better retrieval
            if conversation_history and len(query.split()) > 5:
                reformulation_prompt = f"""
                Based on this conversation history and the latest query, generate a standalone search query that would retrieve the most relevant information to answer the user's question. Make the query comprehensive but focused on retrieving relevant information.
                
                Conversation history: 
                {conversation_history}
                
                Latest query: {query}
                
                Rewritten search query:"""
                
                rewritten_query = prompt_llm(reformulation_prompt)
            else:
                rewritten_query = query
                
            return rewritten_query, expanded_terms
        else:
            # For simple queries, just return as is
            return query, []
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
        query: Original query text
        alpha: Weight for vector results (1-alpha for keyword results)
        
    Returns:
        list: Combined and reranked results
    """
    try:
        # Create a dictionary to combine scores
        combined_results = {}
        
        # Process vector results
        for i, result in enumerate(vector_results):
            segment_id = result.get('meta_data', {}).get('segment_id', f"v_{i}")
            combined_results[segment_id] = {
                'item': result,
                'vector_score': result.get('similarity_score', 0),
                'keyword_score': 0,
                'rank_vector': i,  # Lower rank is better
            }
        
        # Process keyword results
        for i, result in enumerate(keyword_results):
            segment_id = result.get('meta_data', {}).get('segment_id', f"k_{i}")
            if segment_id in combined_results:
                # Update existing entry
                combined_results[segment_id]['keyword_score'] = result.get('bm25_score', 0)
                combined_results[segment_id]['rank_keyword'] = i
            else:
                # Create new entry
                combined_results[segment_id] = {
                    'item': result,
                    'vector_score': 0,
                    'keyword_score': result.get('bm25_score', 0),
                    'rank_vector': len(vector_results) + 1,  # Penalty for not being in vector results
                    'rank_keyword': i,
                }
        
        # Calculate combined scores
        for segment_id, data in combined_results.items():
            # Normalize rank-based scoring (lower is better)
            max_rank_vector = max(len(vector_results), 1)
            max_rank_keyword = max(len(keyword_results), 1)
            
            norm_rank_vector = 1 - (data['rank_vector'] / max_rank_vector)
            norm_rank_keyword = 1 - (data.get('rank_keyword', max_rank_keyword) / max_rank_keyword)
            
            # Combine scores with specified weight
            data['combined_score'] = (alpha * norm_rank_vector) + ((1 - alpha) * norm_rank_keyword)
        
        # Sort by combined score (higher is better)
        sorted_results = sorted(
            combined_results.values(), 
            key=lambda x: x['combined_score'], 
            reverse=True
        )
        
        # Return the original items with combined scores
        return [
            {**r['item'], 'hybrid_score': r['combined_score']} 
            for r in sorted_results
        ]
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
        if not candidates:
            return []
        
        # Extract texts from candidates
        texts = [c.get('content', '') for c in candidates]
        
        # Create TF-IDF representations for the query and texts
        # This is a simple reranking approach - could be replaced with cross-encoder models
        vectorizer = TfidfVectorizer(stop_words='english')
        try:
            tfidf_matrix = vectorizer.fit_transform(texts + [query])
        except:
            # If empty strings or other issues, just return original ranking
            return candidates[:top_k]
        
        # Calculate cosine similarity between query and each text
        query_vector = tfidf_matrix[-1]
        text_vectors = tfidf_matrix[:-1]
        similarities = cosine_similarity(query_vector, text_vectors)[0]
        
        # Create list of (index, similarity score) tuples and sort by score
        scored_indices = [(i, float(score)) for i, score in enumerate(similarities)]
        scored_indices.sort(key=lambda x: x[1], reverse=True)
        
        # Reorder candidates based on new ranking and add score
        reranked_candidates = []
        for i, score in scored_indices[:top_k]:
            candidate = candidates[i].copy()
            candidate['rerank_score'] = score
            reranked_candidates.append(candidate)
        
        return reranked_candidates
        
    except Exception as e:
        logger.error(f"Error in reranking: {e}")
        # Fallback to original ranking
        return candidates[:top_k]

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
        if not chunks:
            return []
        
        # Sort chunks by relevance score (assuming higher is better)
        sorted_chunks = sorted(chunks, key=lambda x: x.get('similarity_score', 0), reverse=True)
        
        # Function to estimate token count (rough approximation)
        def estimate_tokens(text):
            # Very rough approximation: ~1 token per 4 chars for English text
            return len(text) // 4
        
        # Select chunks until we hit token budget
        selected_chunks = []
        current_tokens = 0
        
        # Always include the most relevant chunk
        if sorted_chunks:
            top_chunk = sorted_chunks[0]
            top_chunk_content = top_chunk.get('content', '')
            top_chunk_tokens = estimate_tokens(top_chunk_content)
            
            # If the single most relevant chunk is already over budget, trim it
            if top_chunk_tokens > token_budget:
                # Simple truncation strategy - could be improved 
                top_chunk = top_chunk.copy()
                truncated_content = top_chunk_content[:token_budget*4]
                top_chunk['content'] = truncated_content
                top_chunk['truncated'] = True
                selected_chunks.append(top_chunk)
                return selected_chunks
            
            selected_chunks.append(top_chunk)
            current_tokens += top_chunk_tokens
            sorted_chunks = sorted_chunks[1:]
        
        # Strategy: Mix high relevance with diversity
        # Take chunks alternating between high relevance and different documents
        seen_sources = set()
        if selected_chunks:
            first_source = selected_chunks[0].get('file_name', '')
            seen_sources.add(first_source)
        
        # Try to add diverse sources while respecting token budget
        for chunk in sorted_chunks:
            chunk_content = chunk.get('content', '')
            chunk_tokens = estimate_tokens(chunk_content)
            source = chunk.get('file_name', '')
            
            # Skip if adding this would exceed the token budget
            if current_tokens + chunk_tokens > token_budget:
                continue
            
            # Prioritize chunks from different sources
            if source not in seen_sources:
                selected_chunks.append(chunk)
                current_tokens += chunk_tokens
                seen_sources.add(source)
                
            # Add more chunks from same source if we still have budget
            elif current_tokens < (token_budget * 0.7):  # Only if we're using less than 70% of budget
                selected_chunks.append(chunk)
                current_tokens += chunk_tokens
                
        return selected_chunks
    
    except Exception as e:
        logger.error(f"Error in smart chunk selection: {e}")
        # Fallback to simple truncation of the list
        if chunks:
            return chunks[:3]  # Return first 3 chunks as fallback
        return []
