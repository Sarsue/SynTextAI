"""
Chunk selection components for RAG systems.
"""

import logging
from typing import List, Dict, Any

from .interfaces import ChunkSelectorInterface

logger = logging.getLogger(__name__)


class SmartChunkSelector(ChunkSelectorInterface):
    """
    Smart chunk selector that optimizes for relevance and diversity within token constraints.
    """
    
    def select(self, chunks: List[Dict], query: str, token_budget: int = 3000) -> List[Dict]:
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
            
            # Select chunks until we hit token budget
            selected_chunks = []
            current_tokens = 0
            
            # Always include the most relevant chunk
            if sorted_chunks:
                top_chunk = sorted_chunks[0]
                top_chunk_content = top_chunk.get('content', '')
                top_chunk_tokens = self._estimate_tokens(top_chunk_content)
                
                # If the single most relevant chunk is already over budget, trim it
                if top_chunk_tokens > token_budget:
                    # Simple truncation strategy - could be improved 
                    top_chunk = top_chunk.copy()
                    truncated_content = top_chunk_content[:token_budget*4]  # Approximate chars to tokens
                    top_chunk['content'] = truncated_content
                    top_chunk['truncated'] = True
                    selected_chunks.append(top_chunk)
                    return selected_chunks
                
                selected_chunks.append(top_chunk)
                current_tokens += top_chunk_tokens
                sorted_chunks = sorted_chunks[1:]
            
            # Strategy: Mix high relevance with diversity
            seen_keys = set()
            if selected_chunks:
                first = selected_chunks[0]
                first_source = first.get('file_name', '')
                first_seg = first.get('segment_id')
                first_page = first.get('page_number')
                first_key = (first_source, first_seg if first_seg is not None else first_page)
                seen_keys.add(first_key)
            
            # Try to add diverse sources while respecting token budget
            for chunk in sorted_chunks:
                chunk_content = chunk.get('content', '')
                chunk_tokens = self._estimate_tokens(chunk_content)
                source = chunk.get('file_name', '')
                seg_id = chunk.get('segment_id')
                page_num = chunk.get('page_number')
                diversity_key = (source, seg_id if seg_id is not None else page_num)
                
                # Skip if adding this would exceed the token budget
                if current_tokens + chunk_tokens > token_budget:
                    continue
                
                # Prioritize diverse segments/pages first (even within the same file)
                if diversity_key not in seen_keys:
                    selected_chunks.append(chunk)
                    current_tokens += chunk_tokens
                    seen_keys.add(diversity_key)
                    
                # Add more chunks from same source if we still have budget
                elif current_tokens < (token_budget * 0.7):  # Only if we're using less than 70% of budget
                    selected_chunks.append(chunk)
                    current_tokens += chunk_tokens
                    
            return selected_chunks
        
        except Exception as e:
            logger.error(f"Error in smart chunk selection: {e}", exc_info=True)
            # Fallback to simple truncation of the list
            if chunks:
                return chunks[:3]  # Return first 3 chunks as fallback
            return []
            
    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate the number of tokens in a text.
        
        Args:
            text: Input text
            
        Returns:
            int: Estimated token count
        """
        # Very rough approximation: ~1 token per 4 chars for English text
        return len(text) // 4
