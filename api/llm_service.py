import time
import os
import requests
from dotenv import load_dotenv
from gpt4all import GPT4All
from requests.exceptions import HTTPError, Timeout, RequestException

# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Timeout settings for HTTP requests
REQUEST_TIMEOUT = (5, 15)  # (connect timeout, read timeout)
RETRY_DELAY = 2  # Seconds between retries
MAX_RETRIES = 3  # Retry limit for requests

def prompt_slm(prompt):
    """Generate a response using GPT4All model."""
    model = GPT4All("Phi-3-mini-4k-instruct.Q4_0.gguf")
    with model.chat_session():
        return model.generate(prompt)

def robust_request(url, headers, data):
    """Send a robust POST request with retries and error handling."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()  # Raise exception on non-2xx responses
            return response.json()  # Return JSON response if successful

        except Timeout:
            print(f"Request timed out. Retrying... ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)

        except HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
            break  # No point in retrying on HTTP errors

        except RequestException as err:
            print(f"Request failed: {err}. Retrying... ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)

    return {"error": "Request failed after multiple retries."}

def get_mistral_response(url, api_key, model_name, user_message):
    """Send request to the Mistral API and handle various models."""
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }

    if 'embeddings' in url:
        data = {"input": user_message, "model": model_name, "encoding_format": "float"}
    elif 'pixtral' in model_name:
        data = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the text from this image"},
                        {"type": "image_url", "image_url": f"data:image/jpeg;base64,{user_message}"}
                    ]
                }
            ],
            "max_tokens": 3000
        }
    else:
        data = {"model": model_name, "messages": [{"role": "user", "content": user_message}]}

    return robust_request(url, headers, data)

def get_text_embedding(input_text):
    """Get text embeddings from the Mistral API."""
    url = "https://api.mistral.ai/v1/embeddings"
    return get_mistral_response(url, mistral_key, "mistral-embed", input_text)

def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""
    url = "https://api.mistral.ai/v1/chat/completions"
    return get_mistral_response(url, mistral_key, "mistral-medium-latest", prompt)

def extract_image_text(base64_image):
    """Extract text from a base64-encoded image using Pixtral."""
    url = "https://api.mistral.ai/v1/chat/completions"
    return get_mistral_response(url, mistral_key, "pixtral-12b-2409", base64_image)
