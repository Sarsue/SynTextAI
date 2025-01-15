from llm_service import prompt_llm, summarize
from web_searcher import WebSearch
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SyntextAgent:
    """Interface for conversing with document and web content."""

    def __init__(self):
        self.relevance_thresholds = {
            "high": 0.8,
            "low": 0.3
        }

    def get_chunk_details(self, result):
        """Format chunk details into clear, digestible content."""
        chunk_details = []
   
        if 'page_number' in result:
            chunk_details.append(f"Page {result['page_number']}, Chunk ID: {result['chunk_id']}, Content: {result['chunk']}")
        elif 'start_time' in result and 'end_time' in result:
            chunk_details.append(f"Time Range ({result['start_time']} - {result['end_time']}), Chunk ID: {result['chunk_id']}, Content: {result['chunk']}")
        else:
            chunk_details.append(f"Chunk ID: {result['chunk_id']}, Content: {result['chunk']}")
        return chunk_details

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

    def assess_relevance_scores(self, query, top_k_results):
        """Evaluate relevance scores for each chunk individually."""
        relevance_scores = []
        for result in top_k_results:
            chunk = self.get_chunk_details(result)
            score = self.assess_relevance(query, chunk)
            relevance_scores.append({
                "type": "chunk",
                "content": chunk,
                "metadata": result.get("metadata"),
                "score": score
            })
        return relevance_scores

    def determine_best_context(self, relevance_scores):
        """Determine the best context based on relevance score."""
        best_context = max(relevance_scores, key=lambda x: x["score"])
        best_score = best_context["score"]
        return best_context, best_score

    def query_pipeline(self, query: str, convo_history: str, top_k_results: list, language: str) -> str:
        """
        Main pipeline to process a user query using document chunks.
        """
        try:
            # Step 1: Evaluate relevance scores for each chunk
            relevance_scores = self.assess_relevance_scores(query, top_k_results)
            print(relevance_scores)

            
        except Exception as e:
            logger.error(f"Exception occurred: {e}", exc_info=True)
            return "Syntext ran into issues processing this query. Please rephrase your question."

if __name__ == "__main__":
    import os
    from docsynth_store import DocSynthStore
    from llm_service import get_text_embeddings_in_batches, get_text_embedding
    
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
    message = "how does a timestamp server work?"
    id = 1
    language = "french"
    topK_chunks = store.query_chunks_by_embedding(id, get_text_embedding(message))
    syntext.query_pipeline(message, None, topK_chunks, language)

