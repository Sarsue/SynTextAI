from llm_service import prompt_llm, prompt_slm

def process_context(query, user_history, files):
    # Construct the prompt to instruct the LLM on how to handle the query
    prompt = f"""
    You are an intelligent assistant. Given the following query and message history and files , determine whether the user is asking for:
    1. Information retrieval (just return "retrieval").
    2. Document summarization (return "summarize [filename]").
    3. Document translation (return "translate [filename] to [language]").

    Ensure that:
    - If the query is a retrieval request, just respond with "retrieval."
    - If the query asks for summarization, identify the document and respond with "summarize [filename]".
    - If the query asks for translation, identify the document and the target language, then respond with "translate [filename] to [language]".
    - If the query is unclear or lacks a document reference where necessary, ask for clarification.

    Query: "{query}"

    User History:
    {user_history}

    Files:
    {files}
    Your Response (choose from "retrieval," "summarize [filename]," or "translate [filename] to [language]"):
    """

    # Get the LLM response
    response = prompt_llm(prompt)

    # Return only the response without explanations
    return response.strip()
if __name__ == '__main__':
    queries = ["Can you summarize the annual report?", "can you return this document in spanish?", "how many languages do we have this document translated into?"]

    user_history =["What was the total revenue for the organization?", "Revenue was $300M"]
    files = ["annual_report.pdf"]
    for query in queries:
        response = process_context(query,user_history,files)
        print(response)
        user_history.append(query)
        user_history.append(response)