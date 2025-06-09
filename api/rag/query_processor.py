"""
Query processing module for enhancing RAG queries.
"""

import logging
from typing import List, Tuple, Optional

from .interfaces import QueryProcessorInterface

logger = logging.getLogger(__name__)

# Try to import LLM service function
try:
    from ..llm_service import prompt_llm
except ImportError:
    logger.warning("Could not import from llm_service, defining fallback function")
    def prompt_llm(text):
        logger.error("llm_service.prompt_llm not available")
        return "LLM service unavailable. Please check dependencies."


class DefaultQueryProcessor(QueryProcessorInterface):
    """
    Default implementation of query processing with expansion and reformulation.
    """

    def process(self, query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
        """
        Process and expand the query to improve retrieval quality.
        
        Args:
            query: The original user query
            conversation_history: Optional conversation history for context
            
        Returns:
            tuple: (processed_query, expanded_terms)
        """
        # For trivial queries, just return as is
        if len(query) <= 10:
            return query, []
            
        try:
            # Query expansion with synonyms and related terms
            expanded_terms = self._expand_query(query)
            
            # For complex queries with context, try reformulation
            if conversation_history and len(query.split()) > 5:
                rewritten_query = self._rewrite_query(query, conversation_history)
            else:
                rewritten_query = query
                
            return rewritten_query, expanded_terms
        except Exception as e:
            logger.error(f"Error in query processing: {e}", exc_info=True)
            return query, []  # Fallback to original query
            
    def _expand_query(self, query: str) -> List[str]:
        """Generate expanded search terms for the query."""
        expansion_prompt = f"""
        For the following question, provide 3-5 additional relevant search terms or phrases that would help retrieve relevant information.
        Format the output as a comma-separated list of individual terms or short phrases.
        
        Original query: {query}
        
        Related search terms:"""
        
        try:
            expanded_terms_text = prompt_llm(expansion_prompt)
            return [term.strip() for term in expanded_terms_text.split(',') if term.strip()]
        except Exception as e:
            logger.error(f"Query expansion failed: {e}", exc_info=True)
            return []
            
    def _rewrite_query(self, query: str, conversation_history: str) -> str:
        """Rewrite query using conversation context."""
        reformulation_prompt = f"""
        Based on this conversation history and the latest query, generate a standalone search query that would retrieve the most relevant information to answer the user's question. Make the query comprehensive but focused on retrieving relevant information.
        
        Conversation history: 
        {conversation_history}
        
        Latest query: {query}
        
        Rewritten search query:"""
        
        try:
            return prompt_llm(reformulation_prompt).strip()
        except Exception as e:
            logger.error(f"Query reformulation failed: {e}", exc_info=True)
            return query  # Fallback to original query
