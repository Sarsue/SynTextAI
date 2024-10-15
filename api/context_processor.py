from llm_service import prompt_llm
from requests.exceptions import Timeout
import logging
import time

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

LLM_CONTEXT_WINDOW = 3000
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

def get_sources(topic, belief_system):
    """Retrieve relevant sources for a given topic and belief system."""
    return topic_sources.get(topic, {}).get(belief_system, [])
def chunk_text(text, max_tokens=LLM_CONTEXT_WINDOW):
    """Split text into chunks fitting within the LLM's context window."""
    words = text.split()
    return [" ".join(words[i:i + max_tokens]) for i in range(0, len(words), max_tokens)]

def retry_with_delay(func, *args, **kwargs):
    """Retry function with exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Timeout as e:
            logging.warning(f"Timeout occurred: {e}. Retrying {attempt + 1}/{MAX_RETRIES}...")
            time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            logging.error(f"Error: {e}")
            raise
    raise Timeout(f"Failed after {MAX_RETRIES} retries.")

def classify_content(content, topics_list):
    """Classify the content using the LLM."""
    classification_prompt = f"""
    Given the following content:

    {content[:2000]}  # Trim to avoid overflow in prompt

    ### Topics:
    {', '.join(topics_list)}

    Please classify the content under one of the topics above. 
    If the content doesn’t align with any topic, respond with "out of scope."
    """
    response = retry_with_delay(prompt_llm, classification_prompt)
    topic = response['choices'][0]['message']['content'].strip().lower()
    return topic.replace('-', '').replace(',', '').strip()

def generate_interpretation(content_chunk, topic, sources_list, belief_system):
    """Generate an interpretation of a content chunk using the LLM."""
    # Extend the prompt to encourage broader interpretation and exploration of ideas
    prompt = f"""
    The content is classified under the topic: **{topic}**.

    Based on this content, provide a thoughtful and conversational interpretation.
    While weaving in insights from **2-4 relevant sources** from the list below (aligned with the belief system: {belief_system}),
    feel free to draw on additional knowledge or perspectives that might enrich the interpretation.

    ### Content Chunk:
    {content_chunk}

    ### Relevant Sources:
    {', '.join(sources_list)}

    ### Belief System:
    {belief_system}

    Your response should be engaging and supportive, ending with actionable or uplifting advice for the reader.
    """

    response = retry_with_delay(prompt_llm, prompt)

    # Extract the text response from the returned dictionary
    interpretation_text = response['choices'][0]['message']['content'].strip()
    return interpretation_text

def context(content, belief_system='agnostic'):
    """Process file content and generate relevant insights."""
    content_chunks = chunk_text(content)
    topic = classify_content(content_chunks[0], topics_list)
    logging.info(f"Classified Topic: {topic}")

    if topic == "out of scope":
        return "The content is not relevant to the topics SynText covers."
    
    sources_list = get_sources(topic, belief_system)
    
    # Ensure that interpretations is a list of strings
    interpretations = [
        generate_interpretation(chunk, topic, sources_list, belief_system)
        for chunk in content_chunks
    ]

    # Join only strings into a single output
    return "\n\n".join(interpretations)

if __name__ == '__main__':
    # Example usage
    file_data = """
    The document discusses the challenges of maintaining trust in long-term relationships 
    and offers strategies to rebuild intimacy after emotional distance.
    """
    response = context(file_data, belief_system='agnostic')
    print(response)
