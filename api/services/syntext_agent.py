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


_NO_ANSWER = (
    "I couldn't find enough evidence in your documents to answer that confidently. "
    "Please rephrase your question or ask about a more specific section."
)


def _declined(text: str) -> bool:
    """Did the model use the word it was told to use when it has no answer?

    Matched against the opening of the reply rather than anywhere in it, so an
    answer that happens to discuss insufficient rainfall is not read as a
    refusal.
    """
    return (text or "").strip().upper().lstrip("*#_ ").startswith("INSUFFICIENT")


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
    
    async def query_pipeline(self, query: str, convo_history: str, top_k_results: List[Dict], language: str, comprehension_level: str) -> str:
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
                        history_summary = await generate_explanation(summarization_prompt, language=language, comprehension_level=comprehension_level)
                        history_prompt = f"\n\nPrevious Conversation Summary:\n{history_summary}\n\n"
                    except Exception as e:
                        logger.warning(f"Failed to summarize conversation history: {e}")
                        # Truncate if summarization fails
                        history_prompt = f"\n\nPrevious Conversation (truncated):\n{convo_history[:1500]}...\n\n"
                else:
                    history_prompt = f"\n\nPrevious Conversation History:\n{convo_history}\n\n" if convo_history else ""
                
                # Step 3: Grounding contract first, then citation mechanics.
                #
                # The grounding rule used to sit at item 4 of a numbered list
                # about formatting, after three rules about where to put
                # brackets. Retrieval always returns its best twenty-five pages,
                # because a search ranks, it does not judge, and a question about
                # registering a trademark scores higher against a shelf of
                # small-business guidance than several questions the documents
                # genuinely answer. Asked that, the model wrote a confident
                # USPTO walkthrough that appears nowhere in the customer's
                # documents. Nothing in the retrieved text can prevent that. Only
                # this instruction can, so it goes first and it is concrete.
                citation_instruction = (
                    "Answer ONLY from the numbered context segments below.\n\n"
                    "FIRST, decide whether the segments actually answer the question that "
                    "was asked. Being about the same broad subject is not enough: segments "
                    "about running a small business do not answer a question about "
                    "registering a trademark. If the specific answer is not in the "
                    "segments, reply with the single word INSUFFICIENT and nothing else. "
                    "Do not fill the gap from anything you know outside the segments, and "
                    "do not offer general guidance instead.\n\n"
                    "If the segments DO answer it, answer in full. Being strict about "
                    "grounding is not a reason to be brief: give the specific figures, "
                    "deadlines and terms the documents themselves use, and draw on every "
                    "segment that bears on the question rather than the first one that "
                    "fits.\n"
                    "1. ALWAYS cite the segment number using [Segment N] format immediately after the information.\n"
                    "2. For information from multiple segments, cite all relevant segments: [Segment N, M, P].\n"
                    "3. If directly quoting text, use quotation marks and include page or timestamp: \"quoted text\" [Segment N, timestamp 3:45].\n"
                    "4. If the segments answer part of the question but not all of it, answer that part and say plainly which part they do not cover.\n"
                    "5. When citing timestamps or page numbers, be precise and only reference what actually appears in the context.\n"
                    "6. If the question has several parts, cover every part the segments support, and cite the source of each."
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
                    # Reduction renumbers the segments from 1, so the range the
                    # citation check validates against has to shrink with it.
                    # Left at the original count, [Segment 20] passed validation
                    # against a prompt that only ever showed six.
                    top_k_results = reduced_chunks

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
                    prompt_tokens = token_count(full_prompt)
                    if prompt_tokens > MAX_TOKENS_CONTEXT:
                        # generate_explanation enforces the budget in tokens, so
                        # this only needs to say that reduction was not enough.
                        # It used to cut at MAX_TOKENS_CONTEXT * 4 characters,
                        # a number four times the real window that could never
                        # bring an over-long prompt back inside it.
                        logger.error(
                            f"Prompt still exceeds the token limit after reduction "
                            f"({prompt_tokens}/{MAX_TOKENS_CONTEXT}); it will be truncated."
                        )

                # Step 7: Call the LLM with the combined context and instructions
                llm_answer_with_citations = await generate_explanation(
                    full_prompt,
                    language=language,
                    comprehension_level=comprehension_level,
                    max_context_tokens=MAX_TOKENS_CONTEXT
                )

                if not llm_answer_with_citations:
                    logger.error("No response generated from LLM")
                    return "Sorry, I couldn't generate a response. Please try again."

                if _declined(llm_answer_with_citations):
                    # It read the pages and said they do not answer the question.
                    # Take it at its word instead of pressing for an answer: the
                    # retry path exists for a missing citation marker, not for a
                    # verdict already given.
                    logger.info("Model declined: retrieved context does not answer the question")
                    return _NO_ANSWER

                # Step 7.5: Validate citation format. If we provided context, we require at least
                # one valid [Segment N] citation, and N must refer to an existing segment.
                num_segments = len(top_k_results)
                cited = _cited_segments(llm_answer_with_citations)
                uncited_answer = False
                if num_segments > 0:
                    if not cited:
                        # The model answered but omitted the citation markers.
                        # Ask once more, requirement first this time: appending
                        # the reminder to the end of a prompt already holding
                        # twenty-five pages buried it.
                        logger.info("LLM response missing citations; retrying once with an explicit reminder")
                        retry_prompt = (
                            "Your previous answer was rejected because it carried no citations.\n"
                            "Rewrite it so that every factual claim is followed by the marker of the "
                            "segment it came from, written exactly as [Segment N], using only the "
                            "segments below. If none of them support an answer, reply with the single "
                            "word INSUFFICIENT and nothing else.\n\n"
                            + full_prompt
                        )
                        retry = await generate_explanation(
                            retry_prompt,
                            language=language,
                            comprehension_level=comprehension_level,
                            max_context_tokens=MAX_TOKENS_CONTEXT,
                        ) or ""
                        retry_cited = _cited_segments(retry)
                        if _declined(retry):
                            # The model read the pages and judged that none of
                            # them answer the question. That is the one case
                            # where saying so is true.
                            logger.info("Model judged the retrieved context insufficient")
                            return _NO_ANSWER
                        elif retry_cited:
                            llm_answer_with_citations = retry
                            cited = retry_cited
                        else:
                            # An answer with no markers, twice. Discarding it and
                            # reporting no evidence told customers their document
                            # did not contain something that was sitting at the
                            # top of the retrieved set, which is the worst thing
                            # this pipeline can say. Keep the answer, and be
                            # straight about what we could not establish rather
                            # than attaching page links it never claimed.
                            logger.info("Answer has no citation markers after retry; returning it unattributed")
                            uncited_answer = True

                    # Out-of-range markers used to void the whole answer. Drop
                    # just those; an answer citing segments 2 and 99 still has
                    # segment 2 behind it.
                    invalid = [idx for idx in cited if idx < 1 or idx > num_segments]
                    if invalid:
                        logger.info(f"Dropping out-of-range citations: {invalid}")
                        cited = [idx for idx in cited if idx not in invalid]
                        if not cited:
                            uncited_answer = True

                if uncited_answer:
                    consulted = []
                    for idx in range(1, min(num_segments, 3) + 1):
                        entry = source_targets.get(idx)
                        if entry and entry[1] not in [c[1] for c in consulted]:
                            consulted.append(entry)
                    pages = "\n".join(f"- [{text}]({target})" for text, target in consulted)
                    return (
                        llm_answer_with_citations
                        + "\n\n_I could not tie each statement above to a specific page. "
                        "These are the pages I searched, so you can check it yourself:_\n"
                        + pages
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