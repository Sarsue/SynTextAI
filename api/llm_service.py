import time
import os
import requests
from dotenv import load_dotenv
from gpt4all import GPT4All
from requests.exceptions import RequestException

# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Timeout settings for HTTP requests
REQUEST_TIMEOUT = (5, 60)  # (connect timeout, read timeout)

def prompt_slm(prompt):
    """Generate a response using the GPT4All model."""
    model = GPT4All("Phi-3-mini-4k-instruct.Q4_0.gguf")
    with model.chat_session():
        return model.generate(prompt)

def send_request(url, headers, data):
    """Send a POST request and handle potential errors."""
    try:
        response = requests.post(url, headers=headers, json=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()  # Raise exception on non-2xx responses
        return response.json()  # Return JSON response if successful
    except RequestException as e:
        print(f"Request error: {e}")
        return {"error": str(e)}

def get_mistral_response(url, api_key, model_name, user_message):
    """Send a request to the Mistral API with appropriate model handling."""
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }

    # Prepare data payload based on the model type
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

    # Send request
    return send_request(url, headers, data)

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

# Example usage:
if __name__ == '__main__':
    pass
    # text_prompt = "What is the meaning of life according to Stoic philosophy?"
    # response = prompt_llm(text_prompt)
    # print(response)

    # base64_image = "<your_base
