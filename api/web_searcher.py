import requests
import os
from bs4 import BeautifulSoup
from llm_service import prompt_llm  # Ensure this is correctly defined in your project
import re
import time  # Import time module to add delays

# URL for searxng and Tavily APIs
searxng_url = "http://178.128.236.126:8080/search"
tavily_url = "https://api.tavily.com/search"

def get_best_answer(api_response):
    results = api_response.get('results', [])
    if not results:
        return None, None  # No results available

    # Sort results by score (descending) to get the most relevant one
    best_result = max(results, key=lambda x: x.get('score', 0))
    return best_result.get('content', ''), best_result.get('url', '')

# Function for Tavily search
def tavily_search(query):
    api_key = os.getenv("TAVILY_API_KEY")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {"query": query}
    response = requests.post(tavily_url, headers=headers, json=data)
    
    if response.status_code == 200:
        api_response = response.json()
        answer, url = get_best_answer(api_response)
        print(answer, url)
        return answer, url
    else:
        return None, None


# Function for SearxNG search with timeout
def searxng_search(query, timeout=30):  # Default timeout set to 10 seconds
    params = {"q": query, "format": "json"}
    try:
        response = requests.get(searxng_url, params=params, timeout=timeout)  # Adding timeout
        if response.status_code == 200:
            return response.json()  
        else:
            print(f"Error: {response.status_code}")
            return None
    except requests.exceptions.Timeout:
        print("The request timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None

# Function to fetch page content from URL with timeout
def fetch_page_content(url, timeout=15):  # Default timeout set to 10 seconds
    try:
        response = requests.get(url, timeout=timeout)  # Adding timeout
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Remove href attributes from <a> tags but keep the text
            for a in soup.find_all("a"):
                a.replace_with(a.get_text())

            # Extract text
            text = soup.get_text(separator=" ")

            # Remove excessive whitespace and newlines
            cleaned_text = re.sub(r'\s+', ' ', text).strip()
            return cleaned_text
        else:
            print(f"Error: Unable to fetch {url}, status code: {response.status_code}")
            return None
    except requests.exceptions.Timeout:
        print(f"Timeout occurred while fetching {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

# Function to query the LLM with the page content
def query_llm_with_text(text, query):
    prompt = f"""
    Based on the following text, answer the question: {query}

    {text}

    Please also provide a confidence rating (between 0 and 1) based on how confident you are in the quality and relevance of the text in answering this question. 
    The confidence should be in the format 'Confidence: <score>'.
    """
    # Get the response from the LLM
    response_text = prompt_llm(prompt)

    # Extract the answer (assuming the response is structured to start with the answer)
    answer = response_text.strip()

    # Use regex to find a confidence score in the response text
    score_match = re.search(r"Confidence: (\d\.\d{1,2})", response_text)
    
    # If a confidence score is found, use it; otherwise, default to 0.0
    score = float(score_match.group(1)) if score_match else 0.0

    # Clean the answer by removing the confidence score from the text
    cleaned_answer = re.sub(r"Confidence: \d\.\d{1,2}", "", answer).strip()

    return cleaned_answer, score



# Main function to get answers from the web
def get_answers_from_web(query):
    try:
        # Step 1: Search using searxng (or another search method)
        search_results = searxng_search(query)
        if not search_results:
            return None, None

        # Step 2: Extract top results and fetch page content
        top_results = []
        for result in search_results.get('results', [])[:5]:  # Limit to first 5 results
            url = result.get('url')
            title = result.get('title')
            score = result.get('score', 0)
            
            # Fetch page content
            page_content = fetch_page_content(url)
            if page_content:
                top_results.append({
                    'title': title,
                    'url': url,
                    'content': page_content,
                    'score': score
                })
            time.sleep(1)  # Sleep for 1 seconds between queries

        # Step 3: Query LLM with each result's content
        answers = []
        for result in top_results:
            answer, score = query_llm_with_text(result['content'], query)
            answers.append({
                'title': result['title'],
                'url': result['url'],
                'answer': answer,
                'score': score
            })

            # Introduce a delay between requests to avoid being blacklisted
            time.sleep(1)  # Sleep for 1 seconds between queries

        # Step 4: Rank answers based on LLM's score and return the best match
        if answers:
            best_answer = max(answers, key=lambda x: x['score'])
            
            # Clean the response by removing any confidence score reference
            clean_prompt = f"""
            Clean the following answer by removing any reference to the confidence score. The response should be clear and concise, with no internal information such as a confidence score.

            {best_answer["answer"]}
            """

            # Get the cleaned response from the LLM
            cleaned_response = prompt_llm(clean_prompt)
            
            # Return the cleaned response and the corresponding URL
            return cleaned_response, best_answer["url"]
        else:
            return None, None
    
    except Exception as e:
        print(f"Error occurred: {e}")  # Log the error for debugging
        return None, None

# Function to initiate search and get the best answer
def search(query):
    result, url = get_answers_from_web(query)
    if result == None:
        result, url =  tavily_search(query)
    return result, url


if __name__ == "__main__":
    query = "How do i install air filter in my frigidaire ?"
    print(search(query))

