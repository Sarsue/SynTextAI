from llm_service import prompt_llm

def process_context(query, user_history, files):
    # Construct the prompt to instruct the LLM on how to handle the query
    prompt = f"""
You are an intelligent multilingual assistant. Given the following query, message history, and files, determine the nature of the user's request:

1. **Information Retrieval**: If the user is requesting specific information from the documents. Include the language in the response if detected (e.g., "retrieval in English").
2. **Document Summarization**: If the user is requesting a summary of a document. Include the language and the filename in the response if detected (e.g., "summarize annual_report.pdf in French").
3. **Clarification**: if the user's request isn't clearly information retrieval or summarization.

Ensure that:
- The response should be exactly one of the following formats: "retrieval in [language]" or "summarize [filename] in [language]" or "clarification".
- Do not provide additional explanations, confidence percentages, or clarification requests unless explicitly asked for.

**Example:**
Query: "Peux-tu me donner le résumé ?"
User History: ["how much did we make last quarter?", "according to the financial_report.pdf we made $300M."]
Files: financial_report.pdf, annual_report.pdf

Your Response: "summarize financial_report.pdf in French"

**Now, given the current query:**

Query: "{query}"

User History:
{user_history}

Files:
{files}

Your Response (choose from "retrieval in [language]" or "summarize [filename] in [language]" or "clarification"):
"""

    # Get the LLM response
    response = prompt_llm(prompt).strip()

    print("rcvd: " + response)

    if "clarification" in response:
        task_type = "clarification"
        language = None
        file_name = None
    else:
        # If the response doesn't match any expected format, return clarification
        task_type = "clarification"
        language = None
        file_name = None
    # Check for the occurrence of key phrases

    if "retrieval in" in response:
        parts = response.split("retrieval in")
        language = parts[-1].strip().split()[0]
        task_type = "retrieval"
        file_name = None

    if "summarize" in response and "in" in response:
        parts = response.split(" ")
        language = parts[-1].strip()  # Language is the part after "in"
        
        # Filename is the part before "in", after removing "summarize"
        file_name = parts[1].strip()
        task_type = "summarize"
       
   

    return {
        "task_type": task_type,
        "language": language,
        "file_name": file_name
    }

if __name__ == '__main__':
    queries = [
        "Comment les transactions sont-elles validées dans le réseau Bitcoin?"
    ]
    user_history = []
    files = ["annual_report.pdf", "bitcoin.pdf"]

    for query in queries:
        response = process_context(query, user_history, files)
        print(response)