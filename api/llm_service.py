import os
import logging
from dotenv import load_dotenv
from mistralai.client import MistralClient
from requests.exceptions import Timeout, RequestException
import requests
import tiktoken 
from sentence_transformers import SentenceTransformer
import time
import google.generativeai as genai
import dspy 

# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")

# Initialize Gemini client and MistralAI client (keep for embeddings if needed)
# Note: genai.Client is deprecated; use genai.configure and GenerativeModel
if google_api_key:
    try:
        genai.configure(api_key=google_api_key)
        # We'll instantiate the model later or use it via dspy.Google
        logging.info("Google GenAI configured.")
    except Exception as e:
        logging.error(f"Failed to configure Google GenAI: {e}")
        # Handle lack of configuration appropriately
else:
    logging.warning("GOOGLE_API_KEY not found in environment variables.")

# Initialize Mistral client if key exists
mistral_client = None
if mistral_key:
    mistral_client = MistralClient(api_key=mistral_key)
    logging.info("Mistral client initialized.")
else:
    logging.warning("MISTRAL_API_KEY not found, Mistral client not initialized (needed for embeddings).")


# --- Updated Model and Token Limit for Gemini --- 
# Use gemini-1.5-pro for large context window (check availability/pricing)
# Fallback: gemini-1.0-pro with ~32k limit
# Choose model appropriate for the task. Flash is fast and cheap, good for bulk explanations.
GEMINI_MODEL_NAME = "gemini-1.5-flash" # Or "gemini-1.5-pro"

# Practical token limits depend on the specific model version and task
# Gemini 1.5 Flash has 1M context, Pro has up to 2M in preview
# Set a reasonable limit for explanations, DSPy manages this but good to be aware
MAX_TOKENS_CONTEXT = 1000000 # Can adjust based on specific Gemini model/needs

delay = 1 # Keep delay if needed for rate limiting (less likely needed with Google)
logging.basicConfig(level=logging.INFO)

# --- DSPy Configuration --- >
try:
    gemini_lm = dspy.Google(model=GEMINI_MODEL_NAME, api_key=google_api_key)
    dspy.settings.configure(lm=gemini_lm)
    logging.info(f"DSPy configured successfully with Google model: {GEMINI_MODEL_NAME}")
except Exception as e:
    logging.error(f"Failed to configure DSPy with Google: {e}. Explanations via DSPy may fail.")
    # Depending on requirements, might want to raise an error or have a fallback

# --- DSPy Signature for Explanation --- >
class GenerateExplanation(dspy.Signature):
    """Generates an explanation for the given context."""
    context = dspy.InputField(desc="The text or transcript segment to explain.")
    explanation = dspy.OutputField(desc="An explanation of the context, focusing on the main topic/concept or significance.")

# --- DSPy Predictor for Explanation --- >
explain_predictor = dspy.Predict(GenerateExplanation)

# --- DSPy Signature for Summarization --- >
class SummarizeSignature(dspy.Signature):
    """Generates a concise summary of the provided text.
    Instructions: Create a summary capturing the main points of the document text.
    """
    document_text = dspy.InputField(desc="The full text of the document to summarize.")
    summary = dspy.OutputField(desc="A concise summary of the document.")

def generate_summary_dspy(text_to_summarize: str) -> str:
    """Generates a summary using a configured DSPy module."""
    if not google_api_key:
        logging.error("Google API Key not configured. Cannot generate summary.")
        return "Error: API Key not configured."

    try:
        # Configure LM specifically for this function call, similar to explanation
        # This avoids potential issues with global configuration state if other
        # parts of the app use dspy differently.
        temp_lm = dspy.Google(model=GEMINI_MODEL_NAME, api_key=google_api_key)
        with dspy.context(lm=temp_lm):
            summarizer = dspy.Predict(SummarizeSignature)
            result = summarizer(document_text=text_to_summarize)
            logging.info(f"Successfully generated summary.")
            return result.summary
    except Exception as e:
        logging.error(f"Error generating summary with DSPy: {e}", exc_info=True)
        # Return an error message or None, depending on how caller handles it
        return "Error: Could not generate summary."

# --- Token Counter (Using tiktoken as approximation) ---
# Note: For precise Gemini token counts, use genai.GenerativeModel(MODEL_NAME).count_tokens(text)
# but tiktoken provides a reasonable estimate for checks.
def token_count(text):
    """Approximate token count using tiktoken."""
    # Choose an appropriate encoding, cl100k_base is common
    encoding = tiktoken.get_encoding("cl100k_base") 
    return len(encoding.encode(text))

def extract_image_text(base64_image):
    """Extract text from an image using the Mistral API."""
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mistral_key}",
    }
    data = {
        "model": "pixtral-12b-2409",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What’s in this image?"},
                    {"type": "image_url",
                        "image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]
            }
        ],
        "max_tokens": 1500
    }
    TIMEOUT_SECONDS = 10 
    response = requests.post(url, headers=headers,
                             json=data, timeout=TIMEOUT_SECONDS)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    else:
        logging.error(
            f"Error extracting text: {response.status_code} - {response.text}")
        return ""

def prompt_llm(query):
    """Prompt the configured Google Generative AI model. [DEPRECATED - Use DSPy modules]"""
    logging.warning("Direct call to prompt_llm is deprecated for structured tasks like explanations. Use DSPy modules instead.")

    if not google_api_key:
        logging.error("Google API Key not configured.")
        return "Error: Google API Key not set."
    
    try:
 
        response = client.models.generate_content(
        model= GEMINI_MODEL_NAME,
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

# --- New DSPy-based Explanation Function --- >
def generate_explanation_dspy(text_chunk: str, max_context_length: int = 2000) -> str:
    """Generates an explanation for a text chunk using DSPy and Gemini."""
    if not text_chunk:
        logging.warning("generate_explanation_dspy called with empty text_chunk.")
        return ""

    # Truncate context if necessary (DSPy might handle this, but belt-and-suspenders)
    truncated_chunk = text_chunk[:max_context_length]

    try:
        # Ensure DSPy was configured
        if not dspy.settings.lm:
            logging.error("DSPy LM is not configured. Cannot generate explanation.")
            return "Error: LLM service not configured."
            
        response = explain_predictor(context=truncated_chunk)
        if response and hasattr(response, 'explanation') and response.explanation:
             logging.debug(f"DSPy generated explanation: {response.explanation[:50]}...") # Log snippet
             return response.explanation
        else:
            logging.warning(f"DSPy predictor returned empty or invalid response for chunk: {truncated_chunk[:50]}...")
            return ""

    except Exception as e:
        logging.error(f"Error generating explanation with DSPy: {e}", exc_info=True)
        # Return empty string or a specific error message depending on desired behavior
        return "Error: Failed to generate explanation."

def get_text_embedding(input):
    # Ensure client is initialized before use
    if not mistral_client:
        logging.error("Mistral client not initialized. Cannot get embedding.")
        # Option: raise error or return None/empty list based on expected handling
        return [] # Returning empty list might require downstream checks
        
    try:
        embeddings_batch_response = mistral_client.embeddings(
            model="mistral-embed",
            input=input
        )
        # Add check for data existence
        if embeddings_batch_response.data:
            return embeddings_batch_response.data[0].embedding
        else:
            logging.warning(f"Mistral embedding returned no data for input: {str(input)[:50]}...")
            return []
    except Exception as e:
        logging.error(f"Error getting embedding from Mistral: {e}", exc_info=True)
        return [] # Return empty list on error

# --- Embeddings function (Still uses Mistral, update if needed) ---
def get_text_embeddings_in_batches(inputs, batch_size=10):
    """
    Generate embeddings for a list of inputs in batches using Mistral.
    Update this if you want to use Google's embedding models.
    """
    if not mistral_key:
        logging.error("Mistral API Key not configured for embeddings.")
        return []
    # Ensure client is initialized before use
    if not mistral_client:
        logging.error("Mistral client not initialized. Cannot get embeddings in batch.")
        return []
        
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