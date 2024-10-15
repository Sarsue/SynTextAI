from llm_service import prompt_llm
from requests.exceptions import Timeout
import logging 
import time 

topics = ['growth and well being', 'love and relationships', 'spirituality and mindfulness', 'ethics and values']
topics_list = "\n".join(f"- {topic}" for topic in topics)

sources = {
    "spiritual": ['Bible', 'Quran', 'Torah', 'Talmud', 'Bhavad Gita', 'Tripitaka', 'Tao Te Ching'],
    "secular": ['The Republic', 'Nicomachean Ethics', 'Meditations', 'Beyond Good and Evil', 
                'Man’s Search for Meaning', 'The Way of the Superior Man by David Deida', 
                'Osho: The Book Of Wisdom', 'Secular Humanism: Works by Richard Dawkins or Christopher Hitchens', 'Esther Vilar – The Manipulated Man']
}
LLM_CONTEXT_WINDOW = 3000
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

def chunk_text(text, max_tokens=LLM_CONTEXT_WINDOW):
    """Split text into chunks that fit within the LLM's context window."""
    words = text.split()
    chunks = []
    while words:
        chunk = words[:max_tokens]
        chunks.append(" ".join(chunk))
        words = words[max_tokens:]
    return chunks

def process_context(query, user_history, belief_system='agnostic'):
    # Determine the sources to use based on belief system
    if belief_system == 'spiritual':
        selected_sources = sources["spiritual"]
    elif belief_system == 'secular':
        selected_sources = sources["secular"]
    else:  # 'agnostic' can use both
        selected_sources = sources["spiritual"] + sources["secular"]

    # Format the sources for inclusion in the prompt
    sources_list = "\n".join(f"- {src}" for src in selected_sources)

    prompt = f"""
        Your task is to act as a thoughtful and empathetic assistant. Use the **user's query and history** to determine the topic the user needs guidance with. 
        Depending on the **belief system** (spiritual, secular, or agnostic), select the **best 2-4 sources** from the list provided, and weave them naturally into your response in a **supportive, conversational tone**. 

        Make sure your response:
        1. Clearly reflects the relevant topic based on the query and user history.
        2. Weaves quotes or ideas from 2-4 sources seamlessly into the response. Avoid listing sources mechanically.
        3. Feels conversational, showing empathy and offering meaningful suggestions.
        4. Ends with encouragement or actionable advice (if relevant). 

        ---

        ### Query:
        "{query}"

        ### User History:
        {user_history or "No prior history provided."}

        ### Available Topics:
        {topics_list}

        ### Available Sources (based on belief system):
        {sources_list}

        ### Belief System:
        '{belief_system}'

        ---

        Remember: If the query doesn’t fit any topic or there are no relevant sources, say:  
        "Sorry, I don’t have the right information to help with that at the moment. You might consider seeking additional support or guidance elsewhere."

        Now, please generate the most appropriate response for the user.

        """

    # Get the LLM response
    response = prompt_llm(prompt).strip()
    return response


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
    return prompt_llm(classification_prompt).strip()

def generate_interpretation(content_chunk, topic, sources_list, belief_system):
    """Generate interpretation of a content chunk using the LLM."""
    prompt = f"""
    The content is classified under the topic: **{topic}**.

    Provide an interpretation of this content based on the topic.
    Use **2-4 relevant sources** from the list below (aligned with the belief system: {belief_system}).
    Weave the sources naturally to provide insights.

    ### Content Chunk:
    {content_chunk}

    ### Relevant Sources:
    {sources_list}

    ### Belief System:
    {belief_system}

    Generate a conversational interpretation of this chunk, integrating insights from sources. 
    Conclude with an actionable or supportive message.
    """
    return prompt_llm(prompt).strip()


def retry_with_delay(func, *args, **kwargs):
    """Retry function with delay and exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Timeout as e:
            logging.warning(f"Timeout occurred: {e}. Retrying {attempt + 1}/{MAX_RETRIES}...")
            time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            logging.error(f"Error during execution: {e}")
            raise
    raise Timeout(f"Failed after {MAX_RETRIES} retries.")

def process_file_context(content, belief_system='agnostic'):
    selected_sources = (
        sources["spiritual"] if belief_system == 'spiritual' 
        else sources["secular"] if belief_system == 'secular'
        else sources["spiritual"] + sources["secular"]
    )
    sources_list = "\n".join(f"- {src}" for src in selected_sources)

    first_chunk = chunk_text(content)[0]  # Use the first chunk for classification
    topic = classify_content(first_chunk, topics_list)

    if topic.lower() == "out of scope":
        return "The content is not relevant to the topics SynText covers."

    # Generate interpretations for all chunks
    content_chunks = chunk_text(content)
    interpretations = [
            retry_with_delay(generate_interpretation, chunk, topic, sources_list, belief_system)
            for chunk in content_chunks
        ]

    # Combine interpretations into a single response
    response = "\n\n".join(interpretations)
    return response
    



if __name__ == '__main__':
    # queries = [
    #     "what are the highlights o this book you recommended "
    # ]
    # user_history = ["I saw some messages on my wife phone that have me questioning her fidelity? I am worried about my family and I am upset and almost breaking down","The Relationship Cure by John Gottman and Joan DeClaire - This book offers research-based advice on how to build strong, healthy relationships, including how to repair trust after infidelity"]

    # for query in queries:
    #     response = process_context(query, user_history)
    #     print(response)
    # Example usage
    file_data = """
    The document discusses the challenges of maintaining trust in long-term relationships 
    and offers strategies to rebuild intimacy after emotional distance.
    """
    response = process_file_context(file_data, belief_system='agnostic')
    print(response)