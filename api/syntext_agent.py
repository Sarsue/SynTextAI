from llm_service import prompt_llm, summarize
from web_searcher import WebSearch
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SyntextAgent:
    """Interface for conversing with document and web content."""

    def __init__(self):
        self.searcher = WebSearch()
        self.relevance_thresholds = {"high": 0.7, "low": 0.3}

    def assess_relevance(self, query: str, context: str) -> float:
        """
        Assess the relevance of the given context (chunk or history) for the query.
        Calls an LLM to evaluate similarity.
        """
        prompt = (
            f"Evaluate the relevance of the following context to the query '{query}':\n\n{context}\n\n"
            f"Respond with a relevance score between 0 and 1."
        )
        relevance_score = prompt_llm(prompt)
        try:
            score = float(relevance_score.strip())
        except ValueError:
            score = 0.0  # Default to 0 if the response isn't a valid score
        return score

    def choose_best_answer(self, query: str, top_k_results: list, language: str) -> str:
        """
        Find the most relevant chunk and generate an answer.
        """
        prompt = f"Here are some chunks of text along with their details (Respond in {language}):\n"
        for result in top_k_results:
            if 'page_number' in result:
                chunk_details = f"Chunk: {result['chunk']} (Page: {result['page_number']})"
            elif 'start_time' in result and 'end_time' in result:
                chunk_details = f"Chunk: {result['chunk']} (Start: {result['start_time']} - End: {result['end_time']})"
            else:
                chunk_details = f"Chunk: {result['chunk']}"
            prompt += f"{chunk_details} with ID: {result['chunk_id']}\n"

        prompt += f"Which of the above is the best match for the provided query: '{query}'?\n"
        prompt += "Return only the ID of the best matching chunk."
        best_rank = prompt_llm(prompt)

        # Extract chunk ID from LLM's response
        match = re.search(r'id:\s*(\d+)', best_rank)
        result = None
        if match:
            chunk_id = int(match.group(1))
            result = next((r for r in top_k_results if r['chunk_id'] == chunk_id), None)

        if result is None:
            result = top_k_results[0]  # Default to the first result if no match

        response_prompt = (
            f"Answer the following question based on the provided text (Respond in {language}):\n\n"
            f"{result['chunk']}\n\nQuestion: {query}\n\n"
        )
        answer = prompt_llm(response_prompt)

        file_name = result['file_url'].split('/')[-1]
        if 'page_number' in result:
            file_name += f" page {result['page_number']}"
            file_url = f"{result['file_url']}?page={result['page_number']}"
        elif 'start_time' in result and 'end_time' in result:
            file_name += f" from {result['start_time']} to {result['end_time']}"
            file_url = result['file_url']
        else:
            file_url = result['file_url']

        references = [f"[{file_name}]({file_url})"]
        reference_links = "\n".join(references)

        return answer + "\n\n" + reference_links

    def query_pipeline(self, query: str, convo_history: str, top_k_results: list, language: str) -> str:
        """
        Main pipeline to process a user query using history, document chunks, and web search.
        """
        response = "Syntext Experienced Failure. Please Try Again."
        try:
            # Combine conversation history and top-k results for relevance evaluation
            combined_contexts = [
                {"type": "history", "content": convo_history},
                *[
                    {"type": "chunk", "content": chunk['chunk'], "metadata": chunk}
                    for chunk in top_k_results
                ],
            ]

            # Evaluate relevance scores
            relevance_scores = [
                {
                    "type": context["type"],
                    "content": context["content"],
                    "metadata": context.get("metadata"),
                    "score": self.assess_relevance(query, context["content"])
                }
                for context in combined_contexts
            ]

            # Determine the best approach based on relevance
            best_context = max(relevance_scores, key=lambda x: x["score"])
            best_score = best_context["score"]
            logger.info(f"Best context: {best_context} and score: {best_score}")

            if best_score >= self.relevance_thresholds["high"]:
                # High relevance: Use the most relevant context to answer
                if best_context["type"] == "chunk":
                    response = self.choose_best_answer(query, top_k_results, language)
                else:  # Best context is history
                    response_prompt = (
                        f"Answer the following question based on the conversation history (Respond in {language}):\n\n"
                        f"{convo_history}\n\nQuestion: {query}\n\n"
                    )
                    response = prompt_llm(response_prompt)

            # elif self.relevance_thresholds["low"] <= best_score < self.relevance_thresholds["high"]:
            #     # Medium relevance: Use both the context and web search
            #     web_response = self.searcher.search_topic(query)
            #     response_prompt = (
            #         f"Answer the following question using the provided text and web information (Respond in {language}):\n\n"
            #         f"Text: {best_context['content']}\n\nWeb Info: {web_response}\n\n"
            #         f"Question: {query}\n\n"
            #     )
            #     response = prompt_llm(response_prompt)

            # else:
            #     # Low relevance: Use only web search
            #     web_response = self.searcher.search_topic(query)
            #     response_prompt = (
            #         f"Answer the following question using the provided web information (Respond in {language}):\n\n"
            #         f"Web Info: {web_response}\n\n"
            #         f"Question: {query}\n\n"
            #     )
            #     response = prompt_llm(response_prompt)
        except Exception as e:
            logger.error(f"Exception occurred: {e}", exc_info=True)
        return response
