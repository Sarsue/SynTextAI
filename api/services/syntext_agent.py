import re
import logging
from typing import List, Dict, Any, Tuple

from api.services.llm_service import token_count, MAX_TOKENS_CONTEXT, generate_explanation
from api.rag.pipeline import RAGPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


rag_pipeline = RAGPipeline(config={"search_engine": {"default_alpha": 0.7}})

# The prompt permits [Segment 2], [Segment 2, 3] and [Segment 2, timestamp 3:45],
# but only the first form was ever matched: a combined marker passed validation
# as "no citations at all" and survived into the answer as literal text. The
# leading run of comma-separated numbers is the citation; the trailing `[^\]]*`
# absorbs a timestamp without mistaking "3:45" for segments 3 and 45.
_CITATION_RE = re.compile(r"\[Segments?\s*(\d+(?:\s*,\s*\d+)*)[^\]]*\]", re.IGNORECASE)


def _cited_segments(text: str) -> List[int]:
    """Segment numbers referenced by the answer, in order of first appearance."""
    seen: List[int] = []
    for match in _CITATION_RE.finditer(text or ""):
        for raw in match.group(1).split(","):
            idx = int(raw.strip())
            if idx not in seen:
                seen.append(idx)
    return seen


class SyntextAgent:
    """Interface for conversing with document content using large context LLMs."""

    def __init__(self):
        pass 

    def _format_context_and_sources(self, top_k_results: List[Dict]) -> Tuple[str, Dict[int, tuple]]:
        """Formats retrieved segments for the LLM prompt and maps each segment to
        the label and link a reader would use to check it.

        Deliberately does not build the visible source list: which segments the
        answer actually cites isn't known until the answer exists, and listing
        everything retrieved is what produced four entries for a one-page
        citation.
        """
        context_parts = []
        source_targets: Dict[int, tuple] = {}
        
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
            
            # "Segment 3" is internal plumbing. A customer reading a cited
            # answer needs the document and page, which is the thing they can
            # actually go and check.
            source_targets[segment_id] = (source_text, source_target)

        formatted_context = "\n\n".join(context_parts)
        return formatted_context, source_targets
    
    def query_pipeline(self, query: str, convo_history: str, top_k_results: List[Dict], language: str, comprehension_level: str) -> str:
        """
        Enhanced main pipeline using large context: formats context, prompts LLM to cite sources precisely, 
        appends detailed source map.
        """
        try:
            if top_k_results:
                # Step 1: Format context and generate the source map string with enhanced details
                formatted_context, source_targets = self._format_context_and_sources(top_k_results)
                
                # Step 2: Create a conversational summary if history is too long
                if convo_history and len(convo_history) > 1500:  # If history is long
                    try:
                        summarization_prompt = f"Summarize this conversation history briefly, focusing on the most important points and context needed to answer follow-up questions:\n\n{convo_history}"
                        history_summary = generate_explanation(summarization_prompt, language=language, comprehension_level=comprehension_level)
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
                    reduced_chunks = rag_pipeline.chunk_selector.select(
                        top_k_results,
                        query,
                        token_budget=available_context_tokens,
                    )
                    formatted_context, source_targets = self._format_context_and_sources(reduced_chunks)
                    
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
                llm_answer_with_citations = generate_explanation(
                    full_prompt,
                    language=language,
                    comprehension_level=comprehension_level,
                    max_context_length=MAX_TOKENS_CONTEXT
                )

                if not llm_answer_with_citations:
                    logger.error("No response generated from LLM")
                    return "Sorry, I couldn't generate a response. Please try again."

                # Step 7.5: Validate citation format. If we provided context, we require at least
                # one valid [Segment N] citation, and N must refer to an existing segment.
                num_segments = len(top_k_results)
                cited = _cited_segments(llm_answer_with_citations)
                if num_segments > 0:
                    if not cited:
                        # The model answered but omitted the citation markers. That
                        # is a formatting lapse, not absent evidence, and refusing
                        # outright told the user their documents lacked an answer
                        # that had in fact been found. Ask once more, with the
                        # requirement stated plainly, before giving up.
                        logger.info("LLM response missing citations; retrying once with an explicit reminder")
                        retry_prompt = (
                            full_prompt
                            + "\n\nIMPORTANT: your previous answer omitted citations and was rejected. "
                            "Rewrite it so that every factual claim is followed by the marker of the "
                            "segment it came from, written exactly as [Segment N]. Use only the segments "
                            "provided above. If none of them support an answer, reply with the single "
                            "word: INSUFFICIENT."
                        )
                        retry = generate_explanation(
                            retry_prompt,
                            language=language,
                            comprehension_level=comprehension_level,
                            max_context_length=MAX_TOKENS_CONTEXT,
                        ) or ""
                        cited = _cited_segments(retry)
                        if cited and "INSUFFICIENT" not in retry.upper():
                            llm_answer_with_citations = retry
                        else:
                            logger.info("Still no citations after retry; refusing to answer without evidence")
                            return (
                                "I couldn't find enough evidence in your documents to answer that confidently. "
                                "Please rephrase your question or ask about a more specific section."
                            )

                    invalid = [idx for idx in cited if idx < 1 or idx > num_segments]

                    if invalid:
                        logger.info(f"LLM response has invalid citations (out of range): {invalid}")
                        return (
                            "I couldn't validate the citations for that answer against your documents. "
                            "Please rephrase your question or ask for a direct quote from the document."
                        )

                # Step 8: Replace the internal markers with citations a reader
                # can act on. [Segment 2] means nothing to a dental practice;
                # a link to page 36 of their own handbook is the product. The
                # marker format stays as-is for validation above, so accuracy is
                # unchanged and only what reaches the customer differs.
                #
                # The list must describe the answer, not the retrieval. Numbering
                # it by segment index published every chunk the search returned,
                # so an answer that cited one page still showed "1..4" — four
                # entries for a single document, three of which appear nowhere in
                # the text, and a lone [2] in the body with no [1] above it.
                # Renumber by order of first appearance and keep only what the
                # answer actually leans on. Two segments from the same page are
                # one citation to a reader, so they collapse by target.
                display_number: Dict[int, int] = {}
                target_number: Dict[str, int] = {}
                ordered_sources: List[Tuple[int, str, str]] = []

                for idx in cited:
                    entry = source_targets.get(idx)
                    if not entry:
                        continue
                    text, target = entry
                    if target in target_number:
                        # Same page, already cited under an earlier number.
                        display_number[idx] = target_number[target]
                        continue
                    number = len(ordered_sources) + 1
                    display_number[idx] = number
                    target_number[target] = number
                    ordered_sources.append((number, text, target))

                def _linkify(match):
                    # A combined [Segment 2, 3] becomes the links it stands for,
                    # deduplicated: two segments off one page read as one citation.
                    links = []
                    for raw in match.group(1).split(","):
                        number = display_number.get(int(raw.strip()))
                        if number is None or number in links:
                            continue
                        links.append(number)
                    return "".join(
                        f"[[{n}]]({ordered_sources[n - 1][2]})" for n in links
                    )

                llm_answer_with_citations = _CITATION_RE.sub(
                    _linkify, llm_answer_with_citations
                )

                # The frontend splits the answer on this exact marker to lift the
                # citations into their own styled box and render its own heading.
                source_map = "\n".join(
                    ["\n\n**Sources:**"]
                    + [f"{n}. [{text}]({target})" for n, text, target in ordered_sources]
                )

                final_response = llm_answer_with_citations + "\n\n" + source_map
                
                return final_response

            # No relevant document chunks found
            logger.info("No relevant document chunks found for query.")
            return "I couldn't find relevant information in your documents to answer this question. Please try rephrasing your question or upload additional relevant content."

        except Exception as e:
            logger.error(f"Exception occurred in query pipeline: {e}", exc_info=True)
            return "Syntext ran into issues processing this query. Please try again."


if __name__ == "__main__":
    pass