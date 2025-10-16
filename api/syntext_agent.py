import re
import logging
from typing import List, Dict, Any, Tuple

from api.llm_service import generate_explanation_dspy, token_count, MAX_TOKENS_CONTEXT
from api.web_searcher import get_answers_from_web  # Uncommented for fallback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SyntextAgent:
    """Interface for conversing with document and web content using large context LLMs."""

    def __init__(self):
        pass 

    def _format_context_and_sources(self, top_k_results: List[Dict]) -> Tuple[str, str]:
        """Formats retrieved segments for the LLM prompt and creates a source map."""
        context_parts = []
        source_map_parts = ["\n\n**Sources:**"]
        
        for i, result in enumerate(top_k_results):
            segment_id = i + 1
            content = result.get('content', '')
            file_url_base = result.get('file_url')
            file_name = result.get('file_name', 'Unknown File')
            page_num = result.get('page_number')
            meta = result.get('meta_data', {})
            
            # --- Part 1: Format context for LLM --- 
            context_header = f"--- Context Segment {segment_id} ---"
            context_parts.append(f"{context_header}\n{content}")
            
            # --- Part 2: Create source mapping entry --- 
            source_text = file_name
            source_target = file_url_base if file_url_base else "#"
            
            if meta.get("type") == "video" and meta.get("start_time") is not None:
                start_time = meta.get('start_time')
                end_time = meta.get('end_time')
                time_str = f"{start_time:.1f}s-{end_time:.1f}s"
                source_text += f" ({time_str})"
                if file_url_base:  # Add fragment only if URL exists
                     source_target += f"#t={start_time:.1f}"
            elif page_num:
                source_text += f" (Page {page_num})"
                if file_url_base:
                     source_target += f"#page={page_num}"
            
            source_map_parts.append(f"- **[Segment {segment_id}]**: [{source_text}]({source_target})")
            
        formatted_context = "\n\n".join(context_parts)
        source_map = "\n".join(source_map_parts)
        return formatted_context, source_map
    
    def query_pipeline(self, query: str, convo_history: str, top_k_results: List[Dict], language: str, comprehension_level: str) -> str:
        """
        Enhanced main pipeline using large context: formats context, prompts LLM to cite sources precisely, 
        appends detailed source map. Falls back to web search.
        """
        try:
            if top_k_results:
                # Step 1: Format context and generate the source map string with enhanced details
                formatted_context, source_map = self._format_context_and_sources(top_k_results)
                
                # Step 2: Create a conversational summary if history is too long
                if convo_history and len(convo_history) > 1500:  # If history is long
                    try:
                        summarization_prompt = f"Summarize this conversation history briefly, focusing on the most important points and context needed to answer follow-up questions:\n\n{convo_history}"
                        history_summary = generate_explanation_dspy(summarization_prompt, language=language, comprehension_level=comprehension_level)
                        history_prompt = f"\n\nPrevious Conversation Summary:\n{history_summary}\n\n"
                    except Exception as e:
                        logger.warning(f"Failed to summarize conversation history: {e}")
                        # Truncate if summarization fails
                        history_prompt = f"\n\nPrevious Conversation (truncated):\n{convo_history[:1500]}...\n\n"
                else:
                    history_prompt = f"\n\nPrevious Conversation History:\n{convo_history}\n\n" if convo_history else ""
                
                # Step 3: Enhanced citation instructions with confidence and precision guidelines
                citation_instruction = (
                    "When using information from the provided context segments in your answer: \n" 
                    "1. ALWAYS cite the segment number using [Segment N] format immediately after the information.\n" 
                    "2. For information from multiple segments, cite all relevant segments: [Segment N, M, P].\n" 
                    "3. If directly quoting text, use quotation marks and include page or timestamp: \"quoted text\" [Segment N, timestamp 3:45].\n"
                    "4. If the context doesn't contain sufficient information to answer fully, clearly state what's missing.\n"
                    "5. When citing timestamps or page numbers, be precise and only reference what actually appears in the context.\n"
                    "IMPORTANT: Base your answer ONLY on the provided context segments and conversation history.\n"
                    "Do NOT add information from your general knowledge that is not in the provided segments."
                )

                # Step 4: Adapt detail level based on comprehension level
                detail_instruction = ""
                if comprehension_level.lower() == "beginner":
                    detail_instruction = "Explain concepts simply, define technical terms, and use basic examples."
                elif comprehension_level.lower() == "intermediate":
                    detail_instruction = "Use moderate technical detail and some domain-specific terminology. Provide examples where helpful."
                elif comprehension_level.lower() == "advanced":
                    detail_instruction = "Use precise technical language and domain-specific terminology. Go into depth on complex concepts."
                else:  # Default
                    detail_instruction = "Provide a balanced response with clear explanations."

                # Step 5: Create the full prompt with all components
                full_prompt = (
                    f"{citation_instruction}\n\n"
                    f"Respond in {language}. {detail_instruction}\n"
                    f"{history_prompt}"
                    f"User Question: {query}\n\n"
                    f"Provided Context Segments:\n"
                    f"------------------------\n"
                    f"{formatted_context}\n"
                    f"------------------------\n\n"
                    f"Answer:"  # LLM starts generating here
                )
                
                # Step 6: Check token count and apply smart token management with iterative reduction
                prompt_tokens = token_count(full_prompt)
                if prompt_tokens > MAX_TOKENS_CONTEXT:
                    logger.warning(f"Combined prompt ({prompt_tokens} tokens) exceeds MAX_TOKENS_CONTEXT ({MAX_TOKENS_CONTEXT}). Applying smart token reduction.")
                    # Get token count of fixed parts (everything except context)
                    context_start = full_prompt.find("Provided Context Segments:")
                    context_end = full_prompt.find("------------------------\n\n")
                    non_context = full_prompt[:context_start] + full_prompt[context_end:]
                    non_context_tokens = token_count(non_context)
                    
                    # Calculate available tokens for context
                    available_context_tokens = MAX_TOKENS_CONTEXT - non_context_tokens - 100  # 100 token buffer
                    
                    # Smart truncation targeting most relevant segments
                    from rag_utils import smart_chunk_selection
                    reduced_chunks = smart_chunk_selection(top_k_results, query, available_context_tokens)
                    formatted_context, source_map = self._format_context_and_sources(reduced_chunks)
                    
                    # Rebuild prompt with reduced context
                    full_prompt = (
                        f"{citation_instruction}\n\n"
                        f"Respond in {language}. {detail_instruction}\n"
                        f"{history_prompt}"
                        f"User Question: {query}\n\n"
                        f"Provided Context Segments (reduced due to length):\n"
                        f"------------------------\n"
                        f"{formatted_context}\n"
                        f"------------------------\n\n"
                        f"Answer:"
                    )
                    # Re-check token count after reduction (iterative if needed)
                    prompt_tokens = token_count(full_prompt)
                    if prompt_tokens > MAX_TOKENS_CONTEXT:
                        logger.error(f"Prompt still exceeds token limit after reduction ({prompt_tokens}/{MAX_TOKENS_CONTEXT}). Truncating further.")
                        full_prompt = full_prompt[:MAX_TOKENS_CONTEXT * 4]  # Rough character approximation

                # Step 7: Call the LLM with the combined context and instructions
                llm_answer_with_citations = generate_explanation_dspy(
                    full_prompt,
                    language=language,
                    comprehension_level=comprehension_level,
                    max_context_length=MAX_TOKENS_CONTEXT
                )

                if not llm_answer_with_citations:
                    logger.error("No response generated from LLM")
                    return "Sorry, I couldn't generate a response. Please try again."

                # Step 8: Combine LLM answer with improved source map
                final_response = llm_answer_with_citations + "\n\n" + source_map
                
                return final_response

            # Fallback to Web search if no document results found
            logger.info("No relevant document chunks found, falling back to web search.")
            results, _ = get_answers_from_web(query)
            if results:
                return results  # Assuming web search includes its own sources
            return "Sorry, I couldn't find an answer in your documents or on the web."

        except Exception as e:
            logger.error(f"Exception occurred in query pipeline: {e}", exc_info=True)
            return "Syntext ran into issues processing this query. Please try again."


if __name__ == "__main__":
    pass