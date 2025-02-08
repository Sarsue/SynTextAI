import requests
import os
from bs4 import BeautifulSoup

def get_api_search(query):
    url = "https://api.tavily.com/search"
    api_key = os.getenv("TAVILY_API_KEY")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "query": query
    }

    # Send the POST request
    response = requests.post(url, headers=headers, json=data)

    # Check if the request was successful
    if response.status_code == 200:
       return response.json()
    else:
        return None


def searxng_search(query):
    """Search SearxNG and return JSON results."""
    url = "http://localhost:8080/search"

    params = {
        "q": query,  
        "format": "json"  # Ensures the response is JSON
    }

    try:
        response = requests.get(url, params=params)
        
        # Print raw response for debugging
        print("Response:", response.text)  

        if response.status_code == 200:
            try:
                return response.json()  # Parse JSON directly
            except ValueError:
                print("Error: Response is not valid JSON")
                return None
        else:
            print(f"Error: {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    query = "how does theory of relativity explain reality?"
    
    results = searxng_search(query)

    if results:
        print("Search results:", results)
    else:
        print("No results or an error occurred.")
