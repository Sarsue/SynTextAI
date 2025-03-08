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
from openai import OpenAI
import tiktoken
from sentence_transformers import SentenceTransformer
import time
# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Initialize MistralAI client
mistral_client = MistralClient(api_key=mistral_key)

# Model and Token Limit
MODEL_NAME = "mistral-small-latest"
MAX_TOKENS = 4096  # Example token limit for the model (adjust as needed)
delay = 1
logging.basicConfig(level=logging.INFO)

def get_text_embeddings_in_batches(inputs, batch_size=10):
    """
    Generate embeddings for a list of inputs in batches.
    """
    client = MistralClient(api_key=mistral_key)
    all_embeddings = []

    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        embeddings_batch_response = client.embeddings(
            model="mistral-embed",
            input=batch
        )
        # Extract embeddings for the batch
        embeddings = [response.embedding for response in embeddings_batch_response.data]
        all_embeddings.extend(embeddings)
        time.sleep(delay)

    return all_embeddings


def get_text_embedding(input):
    client = MistralClient(api_key=mistral_key)

    embeddings_batch_response = client.embeddings(
        model="mistral-embed",
        input=input
    )
    return embeddings_batch_response.data[0].embedding



def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""

    messages = [chat_completion.ChatMessage(role="user", content=prompt)]
    response = mistral_client.chat(model=MODEL_NAME, messages=messages)
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

def token_count(text):
    """Count the number of tokens in the text."""
    return  len(text.split()) 

def chunk_text(text, token_limit):
    """Chunk the text into parts that fit within the token limit."""
    chunks = []
    current_chunk = []
    current_length = 0
    for word in text.split():
        word_length = token_count(word)
        if current_length + word_length > token_limit:
            chunks.append(" ".join(current_chunk))
            current_chunk = [word]
            current_length = word_length
        else:
            current_chunk.append(word)
            current_length += word_length

    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def summarize(text, prompt="Summarize:", max_iterations=10):
    """Summarize text by chunking it based on the model's token limit."""
    max_tokens = MAX_TOKENS - token_count(prompt)  # Subtract the token count of the prompt
    token_limit = max_tokens  # Set the token limit to the model's available tokens

    for _ in range(max_iterations):
        # If the text is within the token limit, return it
        if token_count(text) <= token_limit:
            break

        # Chunk the text into parts
        chunks = chunk_text(text, token_limit)

        # Summarize each chunk
        text = "\n\n".join(
            prompt_llm(f"{prompt} {chunk}")
            for chunk in chunks
        )

    return text

def prompt_openai_llm(query):
    API_KEY = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key= API_KEY)
    completion = client.chat.completions.create(
    model="gpt-4o-mini",
    store=True,
    messages=[
        {"role": "user", "content": query}
    ]
    )
    response = completion.choices[0].message
    print(response)
    return response 

if __name__ == "__main__":
    pass
