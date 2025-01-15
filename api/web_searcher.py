import re
import json
import textwrap
import logging
import random
from markdownify import MarkdownConverter
from playwright.sync_api import sync_playwright
import datetime
from llm_service import prompt_llm, summarize  # Import the LLM service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

# Randomized user-agent for better impersonation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
    # Add more User-Agents as needed
]

class WebSearch:
    """Perform searches and fetch web content."""

    def __init__(self, verbose=False, retry_limit=3):
        self.verbose = verbose
        self.retry_limit = retry_limit

    def extract_json_from_markdown(self, markdown_text):
        """Extract JSON from Markdown-formatted string."""
        logging.info(f"Extracting JSON from markdown text: {markdown_text}")
        json_match = re.search(r'\[.*?\]', markdown_text.strip(), re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0).strip())
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON: {e}")
        logging.warning("No JSON found in Markdown.")
        return None

    def format_answer(self, answer, sources):
        """Format the answer with sources."""
        paragraphs = answer.splitlines()
        wrapped_paragraphs = [textwrap.fill(p, width=80) for p in paragraphs if p.strip()]
        sources_str = "\nSources:" + "\n".join([f"* [{title}]({url})" for url, title in sources])
        return "\n".join(wrapped_paragraphs) + "\n\n" + sources_str

    def search_topic(self, topic):
        """Search a topic and format the response using Playwright."""
        try:
            today_prompt = f"Today is {datetime.date.today().strftime('%a, %b %e, %Y')}."
            search_prompt = (
                f"{today_prompt}\nPrepare for this prompt: {topic}\n\n"
                "What 3 Internet search topics would help you answer this question? "
                "Answer in a JSON list only."
            )
            response = prompt_llm(search_prompt)
            logging.info(f"LLM Response: {response}")
            
            # Ensure the response is valid before trying to parse it
            searches = self.extract_json_from_markdown(response)
            if not searches:
                raise ValueError("No valid search topics found in the LLM response.")
            
            logging.info(f"Search Queries: {searches}")

            # Use Playwright for browsing and scraping
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                all_sources = []
                for search_query in searches:
                    search_url = f"https://duckduckgo.com/?q={search_query.replace(' ', '+')}"
                    logging.info(f"Searching URL: {search_url}")
                    page.goto(search_url)

                    # Wait for the page content to load fully
                    page.wait_for_timeout(random.randint(2000, 4000))  # Adjust the delay if needed
                    page.wait_for_selector('div.result__wrap', timeout=10000)  # Increase timeout and change selector

                    # Scraping links and snippets from the results
                    results = page.query_selector_all('div.result__wrap')  # Use a general selector for results
                    if not results:
                        logging.warning(f"No results found for {search_query}.")
                        continue  # Skip to the next search query
                    
                    for result in results:
                        title = result.query_selector('a.result__a').inner_text()
                        url = result.query_selector('a.result__a').get_attribute('href')
                        snippet = result.query_selector('.result__snippet').inner_text()
                        all_sources.append((url, title, snippet))
                
                browser.close()

            if not all_sources:
                raise ValueError("No sources were found during web scraping.")

            # Format and return the answer with sources
            answer = "This is the answer fetched from web search results."  # Adjust as needed
            formatted_answer = self.format_answer(answer, all_sources)
            return formatted_answer

        except Exception as e:
            logging.error(f"Error during topic search: {e}")
            return f"An error occurred: {e}"


# Example usage
if __name__ == "__main__":
    searcher = WebSearch()
    web_response = searcher.search_topic(topic="How much has Arsenal spent on signings under Arteta?")
    print(web_response)
