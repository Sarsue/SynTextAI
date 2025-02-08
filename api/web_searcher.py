import requests
import os


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
    # URL for the SearxNG search engine API
    url = "http://localhost:8888/search"

    # Parameters for the search query
    params = {
        "q": query,          # The query to search for
        "format": "json"     # The response format (json)
    }

    try:
        # Send GET request to SearxNG
        response = requests.get(url, params=params)

        # Check if the response is successful
        if response.status_code == 200:
            return response.json()  # Return the response as JSON
        else:
            print(f"Error: {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    # Example usage
    query = "how does theory of relativity explain reality?"

    xng_result = searxng_search(query)

    if xng_result:
        print("Search results:", xng_result)
    else:
        print("No results or an error occurred.")

    # print(get_api_search(query))