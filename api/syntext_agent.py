import re
import logging
from llm_service import prompt_llm, token_count, MAX_TOKENS_CONTEXT 
from web_searcher import get_answers_from_web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SyntextAgent:
    """Interface for conversing with document and web content using large context LLMs."""

    def __init__(self):
        pass 

    def _format_context_and_sources(self, top_k_results: list) -> tuple[str, str]:
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
                if file_url_base: # Add fragment only if URL exists
                     source_target += f"#t={start_time:.1f}"
            elif page_num:
                source_text += f" (Page {page_num})"
                if file_url_base:
                     source_target += f"#page={page_num}"
            
            source_map_parts.append(f"- **[Segment {segment_id}]**: [{source_text}]({source_target})")
            
        formatted_context = "\n\n".join(context_parts)
        source_map = "\n".join(source_map_parts)
        return formatted_context, source_map
    
    def query_pipeline(self, query: str, convo_history: str, top_k_results: list, language: str, comprehension_level: str) -> str:
        """
        Main pipeline using large context: formats context, prompts LLM to cite sources, 
        appends source map. Falls back to web search.
        """
        try:
            if top_k_results:
                # Step 1: Format context and generate the source map string
                formatted_context, source_map = self._format_context_and_sources(top_k_results)
                
                # Step 2: Construct the full prompt with citation instructions
                history_prompt = f"\n\nPrevious Conversation History:\n{convo_history}\n\n" if convo_history else ""
                
                # **** Instruction for LLM ****
                citation_instruction = (
                    "When you use information from the provided context segments in your answer, " 
                    "you MUST cite the segment number(s) using the format [Segment N] or [Segment N, M] " 
                    "immediately after the information. For example: 'Attention mechanisms allow focus [Segment 1].' " 
                    "or 'Self-attention relates positions [Segment 1, 2].' " 
                    "Base your answer *only* on the provided context segments and conversation history."
                 )
                # ***************************

                full_prompt = (
                    f"{citation_instruction}\n\n"
                    f"Respond in {language}. The user desires a {comprehension_level} level of detail.\n"
                    f"{history_prompt}"
                    f"User Question: {query}\n\n"
                    f"Provided Context Segments:\n"
                    f"------------------------\n"
                    f"{formatted_context}\n"
                    f"------------------------\n\n"
                    f"Answer:" # LLM starts generating here
                )
                
                # Step 3: Check token count (optional but recommended)
                prompt_tokens = token_count(full_prompt)
                if prompt_tokens > MAX_TOKENS_CONTEXT:
                    logger.warning(f"Combined prompt ({prompt_tokens} tokens) exceeds MAX_TOKENS_CONTEXT ({MAX_TOKENS_CONTEXT}). Truncation/errors may occur.")
                    # Consider more robust handling like erroring or selective context reduction

                # Step 4: Call the LLM with the combined context and instructions
                llm_answer_with_citations = prompt_llm(full_prompt)

                # Step 5: Combine LLM answer (with citations) and the source map
                final_response = llm_answer_with_citations + source_map
                
                return final_response

            # Step 6: Fallback to Web search if no document results found
            logger.info("No relevant document chunks found, falling back to web search.")
            results, _ = get_answers_from_web(query)
            if results:
                return results # Assuming web search includes its own sources
            return "Sorry, I couldn't find an answer in your documents or on the web."

        except Exception as e:
            logger.error(f"Exception occurred in query pipeline: {e}", exc_info=True)
            return "Syntext ran into issues processing this query. Please try again."


if __name__ == "__main__":
    pass
