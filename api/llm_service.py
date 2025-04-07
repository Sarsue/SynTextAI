import os
import logging
from dotenv import load_dotenv
from mistralai.client import MistralClient
from requests.exceptions import Timeout, RequestException
import requests
import tiktoken 
from sentence_transformers import SentenceTransformer
import time
from google import genai

# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")


# Initialize Gemini client and MistralAI client (keep for embeddings if needed)
client = genai.Client(api_key=google_api_key)
mistral_client = MistralClient(api_key=mistral_key)

# --- Updated Model and Token Limit for Gemini --- 
# Use gemini-1.5-pro for large context window (check availability/pricing)
# Fallback: gemini-1.0-pro with ~32k limit
MODEL_NAME = "gemini-2.0-flash" # Or "gemini-1.0-pro"
# Gemini 1.5 Pro has up to 1 million tokens context window
# Set a practical limit slightly lower to account for prompt, output, safety margins
MAX_TOKENS = 1000000 # Adjust if using 1.0 Pro (e.g., 30000)
delay = 1 # Keep delay if needed for rate limiting (less likely needed with Google)
logging.basicConfig(level=logging.INFO)

# --- Token Counter (Using tiktoken as approximation) ---
# Note: For precise Gemini token counts, use genai.GenerativeModel(MODEL_NAME).count_tokens(text)
# but tiktoken provides a reasonable estimate for checks.
def token_count(text):
    """Approximate token count using tiktoken."""
    # Choose an appropriate encoding, cl100k_base is common
    encoding = tiktoken.get_encoding("cl100k_base") 
    return len(encoding.encode(text))

def prompt_llm(query):
    """Prompt the configured Google Generative AI model."""

    if not google_api_key:
        logging.error("Google API Key not configured.")
        return "Error: Google API Key not set."
    
    try:
 
        response = client.models.generate_content(
        model= MODEL_NAME,
        contents=query
        )
       # print(response.text)

        print(response.model_dump_json(
            exclude_none=True, indent=4))
        return response.text

    except Exception as e:
        logging.error(f"Error interacting with Google GenAI: {e}", exc_info=True)
        # Consider more specific error handling based on google.api_core.exceptions
        return f"Error: Could not get response from LLM. {e}"

# --- Embeddings function (Still uses Mistral, update if needed) ---
def get_text_embeddings_in_batches(inputs, batch_size=10):
    """
    Generate embeddings for a list of inputs in batches using Mistral.
    Update this if you want to use Google's embedding models.
    """
    if not mistral_key:
        logging.error("Mistral API Key not configured for embeddings.")
        return []
    # Assuming mistral_client is already initialized globally
    all_embeddings = []
    max_retries = 3
    retry_delay = 2 # seconds
    
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        retries = 0
        success = False
        while retries < max_retries and not success:
            try:
                embeddings_batch = mistral_client.embeddings(model="mistral-embed", input=batch)
                all_embeddings.extend([emb.embedding for emb in embeddings_batch.data])
                success = True
            except Exception as e:
                retries += 1
                logging.warning(f"Error getting embeddings for batch starting at index {i} (Attempt {retries}/{max_retries}): {e}")
                if retries < max_retries:
                    logging.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"Failed to get embeddings for batch starting at index {i} after {max_retries} attempts.")
                    # Option 1: Re-raise the last exception to signal critical failure
                    raise e 
                    # Option 2: Return partially collected embeddings (might be risky)
                    # return all_embeddings 
                    # Option 3: Append placeholders (like None) or skip the batch
                    # all_embeddings.extend([None] * len(batch)) # Mark failures
                    # break # Stop processing further batches if one fails critically
                    
        # If a batch failed critically and we didn't re-raise, handle here
        # if not success:
            # Handle critical failure after retries if needed (e.g., if Option 3 above was chosen)
            # pass
            
        # Apply the original delay *between batches* if needed for general rate limiting
        time.sleep(delay) # Use the original 'delay' variable if defined elsewhere

    return all_embeddings

if __name__ == "__main__":
   pass