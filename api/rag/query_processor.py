"""
Query processing module for enhancing RAG queries.
"""

import logging
from typing import List, Tuple, Optional


logger = logging.getLogger(__name__)

from ..services.llm_service import gradient_chat


# The floor that keeps a reasoning model from spending its whole budget
# thinking and returning nothing. Defined in llm_service, which enforces it for
# every caller; imported here because this module names it in a signature.
from ..services.llm_service import MIN_COMPLETION_TOKENS


async def prompt_llm(text: str, max_tokens: int = MIN_COMPLETION_TOKENS) -> str:
    """Single-shot prompt used for query expansion and rewriting.

    This previously imported a `prompt_llm` from llm_service that has never
    existed in this codebase, so the ImportError fallback was always taken and
    both callers received an apology string instead of a completion. Expansion
    produced nonsense terms and rewriting silently returned the apology as the
    search query, meaning retrieval has only ever run on the raw question.

    Both uses are short and disposable, so failures degrade to the original
    query rather than propagating: a bad expansion must never be able to make
    retrieval worse than not expanding at all.
    """
    try:
        return await gradient_chat(text, max_tokens=max(max_tokens, MIN_COMPLETION_TOKENS)) or ""
    except Exception as e:
        logger.warning(f"Query-processing prompt failed, continuing without it: {e}")
        return ""


class DefaultQueryProcessor:
    """
    Default implementation of query processing with expansion and reformulation.
    """

    async def process(self, query: str, conversation_history: Optional[str] = None) -> Tuple[str, List[str]]:
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
            expanded_terms = await self._expand_query(query)
            
            # For complex queries with context, try reformulation
            if conversation_history and len(query.split()) > 5:
                rewritten_query = await self._rewrite_query(query, conversation_history)
            else:
                rewritten_query = query
                
            return rewritten_query, expanded_terms
        except Exception as e:
            logger.error(f"Error in query processing: {e}", exc_info=True)
            return query, []  # Fallback to original query
            
    async def _expand_query(self, query: str) -> List[str]:
        """Generate expanded search terms for the query."""
        expansion_prompt = f"""
        For the following question, provide 3-5 additional relevant search terms or phrases that would help retrieve relevant information.
        Format the output as a comma-separated list of individual terms or short phrases.
        
        Original query: {query}
        
        Related search terms:"""
        
        try:
            expanded_terms_text = await prompt_llm(expansion_prompt)
            if not expanded_terms_text.strip():
                return []
            terms = [t.strip() for t in expanded_terms_text.split(',') if t.strip()]
            # Keep them short and few. A model that ignores the format and
            # returns a sentence would otherwise become a search term, and each
            # extra term costs another retrieval round trip.
            terms = [t for t in terms if 0 < len(t.split()) <= 6][:5]
            logger.info(f"Query expansion produced {len(terms)} terms")
            return terms
        except Exception as e:
            logger.error(f"Query expansion failed: {e}", exc_info=True)
            return []
            
    async def _rewrite_query(self, query: str, conversation_history: str) -> str:
        """Rewrite query using conversation context."""
        # "Comprehensive" invited keyword stuffing: a follow-up about a late
        # filing penalty was rewritten into thirty words of loosely related
        # terms, which diffuses retrieval instead of sharpening it. What is
        # wanted is the user's question with its pronouns resolved, not an
        # expansion of it.
        reformulation_prompt = f"""Rewrite the latest question as a short, standalone search query by resolving pronouns and references using the conversation history.

Rules:
- Keep it under 12 words.
- Do not add topics the user did not ask about.
- Reply with the query only, no explanation.

Conversation history:
{conversation_history}

Latest question: {query}

Standalone search query:"""
        
        try:
            rewritten = (await prompt_llm(reformulation_prompt)).strip()
            # Only accept a plausible search query. An empty response, or a
            # model that answers the question or explains itself instead of
            # rewriting, must not become the thing we search for.
            # Reject anything that looks like an expansion rather than a
            # rewrite. A search query longer than the question it came from by a
            # wide margin is keyword soup, and retrieves worse than the original.
            if not rewritten or len(rewritten.split()) > 16:
                logger.info(f"Discarding implausible rewrite ({len(rewritten.split())} words), keeping original")
                return query
            logger.info(f"Rewrote query: {query!r} -> {rewritten!r}")
            return rewritten
        except Exception as e:
            logger.error(f"Query reformulation failed: {e}", exc_info=True)
            return query  # Fallback to original query
