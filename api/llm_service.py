import os
import base64
import time
import asyncio
import logging
from dotenv import load_dotenv
from mistralai.client import MistralClient
from mistralai.models import chat_completion
from requests.exceptions import Timeout, RequestException
import requests
# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Initialize MistralAI client
mistral_client = MistralClient(api_key=mistral_key)

# Constants
LLM_CONTEXT_WINDOW = 8192
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT_SECONDS = 10  # Set timeout for network calls

logging.basicConfig(level=logging.INFO)

# ---- Helper Functions ----


def get_text_embedding(input):
    client = MistralClient(api_key = mistral_key)

    embeddings_batch_response = client.embeddings(
        model="mistral-embed",
        input=input
    )
    return embeddings_batch_response.data[0].embedding



def chunk_text(text, max_tokens=LLM_CONTEXT_WINDOW):
    """Split text into manageable chunks."""
    words = text.split()
    return [" ".join(words[i:i + max_tokens]) for i in range(0, len(words), max_tokens)]

def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""
    model = "mistral-small-latest"
    messages = [chat_completion.ChatMessage(role="user", content=prompt)]
    response = mistral_client.chat(model=model, messages=messages)
    return response.choices[0].message.content.strip()

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
                    {"type": "image_url", "image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]
            }
        ],
        "max_tokens": 1500
    }

    response = requests.post(url, headers=headers, json=data, timeout=TIMEOUT_SECONDS)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    else:
        logging.error(f"Error extracting text: {response.status_code} - {response.text}")
        return ""


def token_count(text):
    """Estimate the number of tokens in the text."""
    # This is a simple approximation; you may need a more accurate tokenizer depending on your LLM.
    return len(text.split())

def truncate_for_context(last_output, new_content, max_tokens):
    """Truncate the last output or new content if necessary to fit within the context window."""
    total_tokens = token_count(last_output) + token_count(new_content)
    
    # If the total exceeds the max tokens, truncate the last output
    while total_tokens > max_tokens:
        last_output = " ".join(last_output.split()[1:])  # Remove the first word
        total_tokens = token_count(last_output) + token_count(new_content)

    return last_output

def syntext(content, last_output, intent, language, comprehension_level):
    """
    Process a single chunk of text, ensuring continuity with the last response.
    """
    # Truncate last output to fit within context window
    last_output = truncate_for_context(last_output, content, LLM_CONTEXT_WINDOW)
    
    # Create a coherent prompt
    prompt = f"""
    ### Intent: {intent.capitalize()}
    Previous Response Context:
    {last_output}

    Now, based on the previous response, please {intent} the following content in {language}, 
    tailored to a comprehension level of a {comprehension_level}.

    ### New Content:
    {content}

    Maintain a similar tone and ensure continuity with the last output.
    """

    return prompt_llm(prompt)


test_prompt = """

User Profile:
- Age: 30
- Gender: Male
- Education Level: Bachelor's Degree
- Occupation: Software Engineer
- Beliefs: Atheist

Media Content:
- Image: [Description of the image content]
- Video: [Transcription or key frames description]
- PDF: [Extracted text or key points]

-Query
- Convo History
-Files History

adjust tone, explanation depth, subject focus based on
"""
