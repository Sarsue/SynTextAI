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

topics = [
    'Physical Health and Nutrition',
    'Mental Health and Stress Management',
    'Emotional Well-Being and Relationships',
    'Spirituality and Inner Harmony',
    'Personal Growth and Self-Actualization',
    'Work-Life Balance and Fulfillment',
    'Community and Social Connections'
]

topics_list = "\n".join(f"- {topic}" for topic in topics)

# Updated sources linked to the new topics
topic_sources = {
    "Physical Health and Nutrition": {
        "spiritual": [
            'Bible', 'Quran', 'Ayurvedic texts', 'Tao Te Ching'
        ],
        "secular": [
            'The Blue Zones: Lessons for Living Longer From the People Who’ve Lived the Longest', 
            'How Not to Die by Michael Greger', 
            'The Omnivore’s Dilemma by Michael Pollan'
        ]
    },
    "Mental Health and Stress Management": {
        "spiritual": [
            'The Power of Now by Eckhart Tolle', 
            'The Tao of Pooh by Benjamin Hoff'
        ],
        "secular": [
            'Mindfulness for Beginners by Jon Kabat-Zinn', 
            'Feeling Good: The New Mood Therapy by David D. Burns'
        ]
    },
    "Emotional Well-Being and Relationships": {
        "spiritual": [
            'The Bible', 'The Quran', 'The Bhagavad Gita'
        ],
        "secular": [
            'The 5 Love Languages by Gary Chapman', 
            'Attached by Amir Levine and Rachel Heller', 
            'Emotional Intelligence by Daniel Goleman'
        ]
    },
    "Spirituality and Inner Harmony": {
        "spiritual": [
            'Tao Te Ching', 'Bhagavad Gita', 'The Upanishads'
        ],
        "secular": [
            'The Four Agreements by Don Miguel Ruiz', 
            'The Gifts of Imperfection by Brené Brown'
        ]
    },
    "Personal Growth and Self-Actualization": {
        "spiritual": [
            'Man’s Search for Meaning by Viktor Frankl', 
            'The Alchemist by Paulo Coelho'
        ],
        "secular": [
            'Atomic Habits by James Clear', 
            'Mindset: The New Psychology of Success by Carol S. Dweck'
        ]
    },
    "Work-Life Balance and Fulfillment": {
        "spiritual": [
            'The Art of Happiness by the Dalai Lama', 
            'The Tao of Pooh by Benjamin Hoff'
        ],
        "secular": [
            'The 7 Habits of Highly Effective People by Stephen R. Covey', 
            'Essentialism: The Disciplined Pursuit of Less by Greg McKeown'
        ]
    },
    "Community and Social Connections": {
        "spiritual": [
            'The Bible', 'The Quran', 'The Art of Loving by Erich Fromm'
        ],
        "secular": [
            'Bowling Alone by Robert D. Putnam', 
            'The Power of Habit by Charles Duhigg'
        ]
    }
}
# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Initialize MistralAI client
mistral_client = MistralClient(api_key=mistral_key)

# Constants
LLM_CONTEXT_WINDOW = 1024
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


def get_sources(topic, belief_system):
    """Retrieve relevant sources for a given topic and belief system."""
    return topic_sources.get(topic, {}).get(belief_system, [])

def chunk_text(text, max_tokens=LLM_CONTEXT_WINDOW):
    """Split text into manageable chunks."""
    words = text.split()
    return [" ".join(words[i:i + max_tokens]) for i in range(0, len(words), max_tokens)]

def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""
    model = "mistral-large-latest"
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


def classify_content(content_chunk):
    """Classify content using the LLM."""
    classification_prompt = f"""
    Given the following content:

    {content_chunk[:2000]}  # Trim to avoid overflow

    ### Topics:
    {', '.join(topics_list)}

    Please classify the content under one of the topics above. 
    If the content doesn’t align with any topic, respond with "out of scope."
    """
    return prompt_llm(classification_prompt)

def generate_interpretation(content_chunk, topic, sources_list, belief_system):
    """Generate interpretation of a content chunk."""
    prompt = f"""
    The content is classified under the topic: **{topic}**.

    Provide a thoughtful interpretation using **2-4 relevant sources** from the belief system: {belief_system}.

    ### Content Chunk:
    {content_chunk}

    ### Relevant Sources:
    {', '.join(sources_list)}

    End with uplifting advice for the reader.
    """
    return prompt_llm(prompt)

def process_content(content, belief_system='agnostic'):
    """Main function to process files."""
    # Step 1: Extract content (e.g., from PDF or image)
    
    # Step 2: Chunk the content for processing
    content_chunks = chunk_text(content)

    # Step 3: Classify the first chunk to determine its topic
    topic = classify_content(content_chunks[0], topics_list)
    logging.info(f"Classified Topic: {topic}")

    if topic == "out of scope":
        return "The content is not relevant to the topics covered."

    # Step 4: Fetch relevant sources
    sources_list = get_sources(topic, belief_system)

    # Step 5: Generate an interpretation for each chunk
    interpretations = []
    for chunk in content_chunks:
        interpretation = generate_interpretation(chunk, topic, sources_list, belief_system)
        interpretations.append(interpretation)

    return "\n\n".join(interpretations)


# ---- Example Usage ----
