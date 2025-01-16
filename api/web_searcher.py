import re
import requests
from bs4 import BeautifulSoup
from readability import Document
from urllib.parse import urlparse, parse_qs

def clean_source_text(text):
    """
    Cleans the input text by removing unnecessary whitespace, tabs, and excessive newlines.
    
    Args:
        text (str): The input text to clean.
    
    Returns:
        str: The cleaned text.
    """
    return (
        text.strip()  # Remove leading and trailing whitespace
        .replace("\t", "")  # Remove all tab characters
        .replace("\n\n", " ")  # Replace double newlines with a space
        .replace("   ", "  ")  # Replace triple spaces with double spaces
        .replace("\n\n\n\n", "\n\n\n")  # Replace 4+ newlines with 3
        .replace("\n+(\s*\n)*", "\n")  # Collapse multiple newlines into one
    )

def search_handler(query, source_count=4):
    try:
        # GET LINKS
        google_search_url = f"https://www.google.com/search?q={query}"
        response = requests.get(google_search_url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = []

        # Extracting links from the search results
        for link_tag in soup.find_all("a"):
            href = link_tag.get("href")
            if href and href.startswith("/url?q="):
                cleaned_href = href.replace("/url?q=", "").split("&")[0]
                if cleaned_href not in links:
                    links.append(cleaned_href)

        # Filter links by excluding certain domains
        exclude_list = ["google", "facebook", "twitter", "instagram", "youtube", "tiktok"]
        filtered_links = []

        for link in links:
            try:
                domain = urlparse(link).hostname
                if not any(excluded in domain for excluded in exclude_list):
                    if not any(urlparse(l).hostname == domain for l in filtered_links):
                        filtered_links.append(link)
            except Exception:
                continue

        final_links = filtered_links[:source_count]

        # SCRAPE TEXT FROM LINKS
        sources = []

        for link in final_links:
            try:
                page_response = requests.get(link, headers={"User-Agent": "Mozilla/5.0"})
                page_response.raise_for_status()

                # Parse the page content using Readability
                doc = Document(page_response.text)
                parsed_content = doc.summary()

                if parsed_content:
                    cleaned_text = clean_source_text(doc.text())
                    sources.append({"url": link, "text": cleaned_text[:1500]})
            except Exception:
                continue

        return {"sources": sources}

    except Exception as e:
        print(f"Error: {e}")
        return {"sources": []}

# Example usage
if __name__ == "__main__":
    query = "How much has Arsenal spent on signings under Arteta?"
    result = search_handler(query)
    print(result)
