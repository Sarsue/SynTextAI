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
logging.basicConfig(level=logging.INFO)

LLM_CONTEXT_WINDOW = 1024 
# ---- Helper Functions ----
from gpt4all import GPT4All
model = GPT4All("Phi-3-mini-4k-instruct.Q4_0.gguf") # downloads / loads a 4.66GB LLM

def get_text_embedding(input):
    pass

def chunk_text(text, max_tokens=LLM_CONTEXT_WINDOW):
    """Split text into manageable chunks."""
    words = text.split()
    return [" ".join(words[i:i + max_tokens]) for i in range(0, len(words), max_tokens)]


def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""
    with model.chat_session():
        response = model.generate(prompt=prompt, max_tokens=1024)
        return response


def extract_image_text(base64_image):
    return f"extract_image_text is under test: {base64_image}"
   


def token_count(text):
    """Estimate the number of tokens in the text."""
    # This is a simple approximation; you may need a more accurate tokenizer depending on your LLM.
    return len(text.split())


def truncate_for_context(last_output, new_content, max_tokens):
    """Truncate the last output or new content if necessary to fit within the context window."""
    total_tokens = token_count(last_output) + token_count(new_content)

    # If the total exceeds the max tokens, truncate the last output
    while total_tokens > max_tokens:
        # Remove the first word
        last_output = " ".join(last_output.split()[1:])
        total_tokens = token_count(last_output) + token_count(new_content)

    return last_output


def syntext(content, last_output, intent, language, comprehension_level):
    """
    Process a single chunk of text, ensuring continuity with the last response.
    """
    # Truncate last output to fit within context window
    last_output = truncate_for_context(
        last_output, content, LLM_CONTEXT_WINDOW)

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


if __name__ == "__main__":
    message = "testing the slm what languages do you know fluently for translation tasks?"
    response = syntext(
        content=message,
        last_output="",
        intent='chat',
        language="English",
        comprehension_level='dropout'
    )
    print(response)

# response_prompt = """

# User Profile:
# - Age: 30
# - Gender: Male
# - Education Level: Bachelor's Degree
# - Occupation: Software Engineer
# - Beliefs: Atheist

# Media Content:
# - Image: [Description of the image content]
# - Video: [Transcription or key frames description]
# - PDF: [Extracted text or key points]

# -Query
# - Convo History
# -Files History

# adjust tone, explanation depth, subject focus based on
# """

# summarize_prompt = """
# Analyze the input text and generate 5 essential questions that when answered , capture the main points and core meaning of the text.
# when formulating your questions address the central theme or argment.
# Identify key supporting ideas
# Highlight important facts or evidence
# Reveal the author's purpose or perspective
# Explore any significant implications or conclusions
# Answer all your generated questions one by one in detail

# """
