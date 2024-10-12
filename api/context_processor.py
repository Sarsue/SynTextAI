from llm_service import prompt_llm

topics = ['growth and well being', 'love and relationships', 'spirituality and mindfulness', 'ethics and values']
topics_list = "\n".join(f"- {topic}" for topic in topics)

sources = {
    "spiritual": ['Bible', 'Quran', 'Torah', 'Talmud', 'Bhavad Gita', 'Tripitaka', 'Tao Te Ching'],
    "secular": ['The Republic', 'Nicomachean Ethics', 'Meditations', 'Beyond Good and Evil', 
                'Man’s Search for Meaning', 'The Way of the Superior Man by David Deida', 
                'Osho: The Book Of Wisdom', 'Secular Humanism: Works by Richard Dawkins or Christopher Hitchens']
}

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






def process_file_context(file_data):
    prompt = f"""
    Given the following file data:

    {file_data}
   
    ### Topics:
    {topics_list}

    Please classify  based on the closest match from the topics above. Provide only the topic name or "out of scope" if the data doesn’t align with any topic.
    """
    response = prompt_llm(prompt).strip()
    return response


if __name__ == '__main__':
    queries = [
        "what are the highlights o this book you recommended "
    ]
    user_history = ["I saw some messages on my wife phone that have me questioning her fidelity? I am worried about my family and I am upset and almost breaking down","The Relationship Cure by John Gottman and Joan DeClaire - This book offers research-based advice on how to build strong, healthy relationships, including how to repair trust after infidelity"]

    for query in queries:
        response = process_context(query, user_history)
        print(response)