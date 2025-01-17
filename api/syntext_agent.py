import re
import logging
from llm_service import prompt_llm, summarize
from web_searcher import WebSearch
from docsynth_store import DocSynthStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SyntextAgent:
    """Interface for conversing with document and web content."""

    def __init__(self):
        self.relevance_thresholds = {
            "high": 0.8,
            "low": 0.3
        }
        self.searcher = WebSearch() 
   

    def assess_relevance(self, query: str, context: str) -> float:
        """
        Assess the relevance of the given context (chunk or history) for the query.
        Calls an LLM to evaluate similarity and extracts a relevance score.

        Args:
            query (str): The query string.
            context (str): The context string.

        Returns:
            float: The relevance score between 0 and 1.
        """
        prompt = (
            f"Evaluate the relevance of the following context to the query '{query}':\n\n{context}\n\n"
            f"Respond with a relevance score between 0 and 1."
        )
        relevance_score: str = prompt_llm(prompt)
        logging.debug(f"LLM Response: {relevance_score}")

        try:
            # Extract the float score from the response
            score_match = re.search(r"\b\d+\.\d+\b", relevance_score)
            if score_match:
                score = float(score_match.group())
            else:
                score = 0.0  # Default to 0 if no valid score is found
        except ValueError:
            score = 0.0  # Default to 0 if an error occurs during parsing

        return score

    def assess_relevance_scores(self, query, top_k_results):
        """Evaluate relevance scores for each chunk individually."""
        relevance_scores = []
        for result in top_k_results:
            segment = result["content"]
            score = self.assess_relevance(query, segment )
            relevance_scores.append({
                "content": segment,
                "meta_data": result.get("meta_data"),
                "cosine_similarity" : result["similarity_score"],
                "relevance_score": score,
                "file_url": result["file_url"],
            })
        return relevance_scores

    def determine_best_context(self, relevance_scores):
        """Determine the best context based on relevance score."""
        best_context = max(relevance_scores, key=lambda x: x["relevance_score"])
        best_score = best_context["relevance_score"]
        return best_context, best_score

    def query_pipeline(self, query: str, convo_history: str, top_k_results: list, language: str) -> str:
        """
        Main pipeline to process a user query using document chunks and relevance scores.
        """
        try:
            # Step 1: Evaluate relevance scores for each chunk
            relevance_scores = self.assess_relevance_scores(query, top_k_results)

            # Step 2: Get the best context based on the highest relevance score
            best_context, best_score = self.determine_best_context(relevance_scores)
            print( best_context, best_score )
            # Step 3: Decide the context based on the relevance score threshold
            if best_score >= self.relevance_thresholds["high"]:
                # Use the full context for high scores
                context = best_context["content"]
          

                # Step 4: Create the LLM prompt with the selected context
                resp_prompt = (
                    f"Answer the following question based on the provided text (Respond in {language}): {context}\n\n"
                    f"Question: {query}\n\n"
                )
                print(resp_prompt)

                # Step 5: Get the answer from the LLM
                ans = prompt_llm(resp_prompt)
           
                # Step 6: Construct the file reference for the selected chunk
                # Assuming best_context['meta_data'] contains either a 'start_time', 'end_time', or 'page_number'
                meta_data = best_context['meta_data']

                # Determine the file type based on metadata
                if meta_data.get("type") == "video":
                        # If it's a video, use start_time and end_time for file_name and file_url
                    file_name = best_context['file_url'].split('/')[-1]
                    if meta_data.get("start_time"):
                        file_name += f" from {meta_data['start_time']} to {meta_data['end_time']}"
                    file_url = f"{best_context['file_url']}?start_time={meta_data['start_time']}&end_time={meta_data['end_time']}"
                else:
                    # If it's a PDF, use page_number for file_name and file_url
                    file_name = best_context['file_url'].split('/')[-1]
                    # if meta_data.get("page_number") > 1:
                    #     pg_num =  meta_data.get("page_number")
                    #     file_name += f" page {pg_num}"
                    # file_url = f"{best_context['file_url']}?page={pg_num}"
                    file_url = best_context['file_url']


                # Step 7: Format the references as clickable links
                reference_links = f"[{file_name}]({file_url})"

                # Step 8: Return the final response with references
                ans +=  "\n\n" + reference_links    
            else:
                ans = self.searcher.search_topic(query)
            
            return ans



               
          

        except Exception as e:
            logger.error(f"Exception occurred: {e}", exc_info=True)
            return "Syntext ran into issues processing this query. Please rephrase your question."


if __name__ == "__main__":
    import os
    from llm_service import get_text_embedding
    import numpy as np
    # Construct paths relative to the base directory
    database_config = {
        'dbname': os.getenv("DATABASE_NAME"),
        'user': os.getenv("DATABASE_USER"),
        'password': os.getenv("DATABASE_PASSWORD"),
        'host': os.getenv("DATABASE_HOST"),
        'port': os.getenv("DATABASE_PORT"),
    }
    DATABASE_URL = (
        f"postgresql://{database_config['user']}:{database_config['password']}"
        f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
    )
    store = DocSynthStore(database_url=DATABASE_URL)
    syntext = SyntextAgent()
    message = "How does attention in LLM work?"
    # id = 1
    # language = "english"
    # query_embedding = get_text_embedding(message)
    # topK_chunks = store.query_chunks_by_embedding(id,query_embedding)
    # response = syntext.query_pipeline(message, None, topK_chunks, language)
    ans = syntext.searcher.search_topic(message)
    print(ans)
