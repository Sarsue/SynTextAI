import os
import logging
from dotenv import load_dotenv
from mistralai.client import MistralClient
from requests.exceptions import Timeout, RequestException
import requests
import tiktoken
import json
import re
from sentence_transformers import SentenceTransformer
import time
import google.generativeai as genai
import dspy
from typing import List

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
gemini_lm = None  # Initialize to None
try:
    # Instantiate dspy.Google with parameters it directly uses for the Google SDK.
    # max_output_tokens is the correct parameter for the Google API.
    gemini_lm = dspy.Google(
        model=GEMINI_MODEL_NAME,
        api_key=google_api_key,
        max_output_tokens=2048  # This is a valid parameter for genai.GenerationConfig
    )
    
    # Configure DSPy settings. dspy.settings.lm.kwargs will reflect gemini_lm.kwargs.
    # It will NOT contain 'max_tokens' at this stage, which is intended.
    dspy.settings.configure(lm=gemini_lm)
    logging.info(f"DSPy configured successfully with Google model: {GEMINI_MODEL_NAME}. LM kwargs from dspy.Google init: {gemini_lm.kwargs}. dspy.settings.lm.kwargs: {dspy.settings.lm.kwargs}")
except Exception as e:
    logging.error(f"Failed to configure DSPy with Google: {e}. Explanations via DSPy may fail.", exc_info=True)
    gemini_lm = None # Explicitly ensure gemini_lm is None if configuration fails

# --- DSPy Signature for Explanation --- >
class GenerateExplanation(dspy.Signature):
    """Generates an explanation for the given context, tailored to the specified language and comprehension level.
    Instructions: Explain the context clearly in the specified language for someone with a {comprehension_level} understanding.
    Focus on the main topic, concept, or significance.
    """
    context = dspy.InputField(desc="The text or transcript segment to explain.")
    language = dspy.InputField(desc="Target language for the explanation (e.g., English, Spanish).", default="English")
    comprehension_level = dspy.InputField(desc="Target comprehension level (e.g., Beginner, Intermediate, Advanced).", default="Beginner")
    explanation = dspy.OutputField(desc="An explanation of the context, tailored to the language and comprehension level.")

# --- DSPy Predictor for Explanation --- >
explain_predictor = dspy.Predict(GenerateExplanation)

# --- DSPy Signature for Key Concept Extraction --- >
class ExtractKeyConcepts(dspy.Signature):
    """You are an expert academic tutor. Your task is to analyze the following {document_type} and extract the key concepts a student should learn from it.
    {content_instruction}
    
    For each key concept, provide:
    1. A concise title for the concept.
    2. A clear and brief explanation of the concept, as if you were explaining it to a student for the first time.
    3. The source location within the document where this concept is most prominently discussed.
        - For PDF documents, provide the page number as an integer (e.g., "source_page_number": 15).
        - For video transcripts, provide the start and end timestamps in seconds (e.g., "source_video_timestamp_start_seconds": 302, "source_video_timestamp_end_seconds": 361).
        - If a precise page or timestamp is not applicable or cannot be determined, use null for that specific source field.

    Please return your response as a JSON array, where each element is an object representing a key concept. Each object should have the following keys:
    - "concept_title": (string) The title of the concept.
    - "concept_explanation": (string) The explanation of the concept.
    - "source_page_number": (integer or null) The page number for PDF sources.
    - "source_video_timestamp_start_seconds": (integer or null) The start timestamp in seconds for video sources.
    - "source_video_timestamp_end_seconds": (integer or null) The end timestamp in seconds for video sources.
    """
    document_content = dspy.InputField(desc="The full text content of the document (PDF text or video transcript).")
    document_type = dspy.InputField(desc="Type of document (e.g., 'PDF document', 'video transcript')", default="document content")
    content_instruction = dspy.InputField(desc="Additional instructions for processing this specific type of content", default="Extract the main concepts, definitions, and key ideas from this content.")
    key_concepts_json = dspy.OutputField(desc="A JSON array of objects, where each object represents a key concept with its title, explanation, and source location (page number or video timestamps).")

# --- DSPy Predictor for Key Concept Extraction --- >
key_concept_extractor = dspy.Predict(ExtractKeyConcepts)

# --- DSPy Signature for Summarization --- >
class SummarizeSignature(dspy.Signature):
    """Generates a concise summary of the provided text, in the specified language and for the target comprehension level.
    Instructions: Create a summary capturing the main points of the document text. Write the summary in {language} for a {comprehension_level} audience.
    """
    document_text = dspy.InputField(desc="The full text of the document to summarize.")
    language = dspy.InputField(desc="Target language for the summary (e.g., English, Spanish).", default="English")
    comprehension_level = dspy.InputField(desc="Target comprehension level (e.g., Beginner, Intermediate, Advanced).", default="Beginner")
    summary = dspy.OutputField(desc="A concise summary of the document, tailored to the language and comprehension level.")

def generate_summary_dspy(text_to_summarize: str, language: str = "English", comprehension_level: str = "Beginner") -> str:
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
            # Provide max_tokens via config to dspy.Predict for dsp.generate to use.
            # Adjust max_tokens if a different limit is desired for summaries.
            summary_module = dspy.Predict(SummarizeSignature, config=dict(max_tokens=2048))
            response = summary_module(document_text=text_to_summarize, language=language, comprehension_level=comprehension_level)
            logging.info(f"Successfully generated summary.")
            return response.summary
    except Exception as e:
        logging.error(f"Error generating summary with DSPy: {e}", exc_info=True)
        # Return an error message or None, depending on how caller handles it
        return "Error: Could not generate summary."

def generate_key_concepts_dspy(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[dict]:
    """Generates key concepts with explanations and source links from document text using DSPy.
    
    Args:
        document_text: The full text of the document.
        language: Target language (currently used by prompt, could be dspy.InputField if needed).
        comprehension_level: Target comprehension level (currently used by prompt).
        
    Returns:
        A list of dictionaries, where each dictionary represents a key concept,
        or an empty list if parsing fails or no concepts are found.
    """
    if not gemini_lm: # Check if DSPy LM is configured
        logging.error("DSPy Google LM not configured. Cannot generate key concepts.")
        return []

    # Truncate document_text if it's too long for the model's context window
    # This is a basic truncation; more sophisticated chunking might be needed for very large docs.
    # DSPy's `dspy.Predict` should handle context limits, but good to be mindful.
    # max_tokens_for_doc = MAX_TOKENS_CONTEXT - 1000 # Reserve some tokens for prompt and output
    # if token_count(document_text) > max_tokens_for_doc:
    #     # This is a placeholder for a more sophisticated truncation/chunking strategy
    #     logging.warning(f"Document text is very long ({token_count(document_text)} tokens) and might be truncated.")
        # document_text = truncate_text_to_tokens(document_text, max_tokens_for_doc) # Implement this if needed

    try:
        logging.info(f"Generating key concepts for {'video transcript' if is_video else 'document text'} (first 100 chars): {document_text[:100]}...")
        
        # Use different prompts for videos vs documents
        if is_video:
            # Customize the key concept extraction for videos to focus on timestamps
            logging.info("Using video-specific key concept extraction method")
            response = key_concept_extractor(
                document_content=document_text,
                document_type="video transcript",
                content_instruction="Focus on the main ideas and concepts presented in this video transcript. Include specific timestamps where possible."
            )
        else:
            # Standard document processing
            response = key_concept_extractor(document_content=document_text)  # Use default prompt
        
        if response and hasattr(response, 'key_concepts_json') and response.key_concepts_json:
            raw_json_output = response.key_concepts_json
            logging.debug(f"DSPy generated key_concepts_json (raw): {raw_json_output[:200]}...")
            
            # Strip markdown fences if present
            match = re.search(r"```(?:json)?\n(.*)\n```", raw_json_output, re.DOTALL)
            if match:
                json_to_parse = match.group(1).strip()
                logging.debug(f"Extracted JSON content: {json_to_parse[:200]}...")
            else:
                json_to_parse = raw_json_output.strip() # Assume it's raw JSON if no fences

            try:
                parsed_concepts = json.loads(json_to_parse)
                if isinstance(parsed_concepts, list):
                    return parsed_concepts
                else:
                    logging.error(f"LLM returned JSON, but it's not a list: {type(parsed_concepts)}")
                    return [] # Or attempt to wrap if it's a single dict meant to be a list
            except json.JSONDecodeError as je:
                logging.error(f"Failed to parse JSON from LLM response: {je}")
                logging.error(f"LLM Raw Output: {response.key_concepts_json}")
                return []
        else:
            logging.warning(f"DSPy predictor returned empty or invalid response for key concepts.")
            return []

    except Exception as e:
        logging.error(f"Error generating key concepts with DSPy: {e}", exc_info=True)
        return [] 

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
def generate_explanation_dspy(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_length: int = 2000) -> str:
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
            
        response = explain_predictor(context=truncated_chunk, language=language, comprehension_level=comprehension_level)
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