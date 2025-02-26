import requests
import os
from bs4 import BeautifulSoup
from llm_service import prompt_llm  # Ensure this is correctly defined in your project
import re
import time  # Import time module to add delays

# URL for searxng and Tavily APIs
searxng_url =  os.getenv("SEARXNG_URL") 
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

def get_search_results(query, k=5, max_tokens_per_source=800):
    """
    Searches the web and returns a list of search results.
    Each result contains title, url, content, and score.
    
    Args:
        query: Search query string
        k: Maximum number of results to return
        max_tokens_per_source: Maximum tokens to keep per source to avoid context length issues
    """
    try:
        search_results = searxng_search(query)
        if not search_results:
            return []

        # Step 2: Extract top results and fetch page content
        top_results = []
        for result in search_results.get('results', [])[:k]:
            url = result.get('url')
            title = result.get('title')
            score = result.get('score', 0)
            
            # Fetch page content
            page_content = fetch_page_content(url)
            if page_content:
                # Truncate content to avoid token limit issues
                # Rough approximation: 1 token ≈ 4 chars
                max_chars = max_tokens_per_source * 4
                if len(page_content) > max_chars:
                    page_content = page_content[:max_chars] + "..."

                top_results.append({
                    'title': title,
                    'url': url,
                    'content': page_content,
                    'score': score
                })
            time.sleep(1)  # Sleep between queries

        return top_results

    except Exception as e:
        print(f"Error in get_search_results: {e}")
        return []

def get_answers_from_web(query):
    try:
        # Step 1: Get search results
        top_results = get_search_results(query)
        if not top_results:
            return None, None
            
        # Step 2: Query LLM with each result's content
        answers = []
        for result in top_results:
            # Create a prompt for this specific source
            prompt = f"""Based on the following source, answer this question: {query}

Source:
Title: {result['title']}
URL: {result['url']}
Content:
{result['content']}

Important instructions:
1. Only include information that is explicitly stated in the source
2. If you're not confident about answering based on this source, indicate low confidence
3. Keep the answer focused and relevant to the query
"""
            answer, score = query_llm_with_text(result['content'], query)
            answers.append({
                'title': result['title'],
                'url': result['url'],
                'answer': answer,
                'score': score
            })

            time.sleep(1)  # Sleep between queries

        # Step 3: Filter answers with good confidence scores and synthesize them
        good_answers = [a for a in answers if a['score'] > 0.6]  # Adjust threshold as needed
        
        if good_answers:
            # Create a prompt to synthesize information from multiple sources
            sources_text = "\n\n".join([
                f"Source {i+1} ({answer['url']}):\n{answer['answer']}"
                for i, answer in enumerate(good_answers)
            ])
            
            synthesis_prompt = f"""Synthesize a comprehensive answer to the question: "{query}"
            
Using information from these sources:

{sources_text}

Instructions:
1. Combine relevant information from all sources
2. Resolve any contradictions between sources
3. Present a clear, coherent answer
4. Keep the response focused and relevant
"""
            
            final_answer = prompt_llm(synthesis_prompt)
            
            # Add reference links at the end
            reference_links = "\n\nReferences:\n" + "\n".join([
                f"- {answer['url']}" for answer in good_answers
            ])
            
            return final_answer + reference_links, None  # Return None for single URL since we're using multiple
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
    query = "how do I make jollof rice?"
    print(get_answers_from_web(query))

