from llm_service import prompt_llm, summarize # Import the LLM service
import re
import json
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from markdownify import MarkdownConverter
import requests
import datetime
import textwrap
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")


safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

class WebSearch:
    """Perform searches and fetch web content."""

    def __init__(self, verbose=False, retry_limit=3):
        self.verbose = verbose
        self.retry_limit = retry_limit
    
    def crawl(self, url, depth=1):
        if depth == 0:
            return []
        response = requests.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        links = [a['href'] for a in soup.find_all('a', href=True)]
        return links + [self.crawl(link, depth-1) for link in links if link.startswith('http')]
        
    def scrape_google(self, query):
        headers = {"User-Agent": "Mozilla/5.0"}
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        response = requests.get(url, headers=headers)
        print(response)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for g in soup.find_all("div", class_="tF2Cxc"):
                title = g.find("h3").text
                link = g.find("a")["href"]
                snippet = g.find("span", class_="aCOpRe").text
                results.append({"title": title, "link": link, "snippet": snippet})
            return results
        else:
            print(f"Error: {response.status_code}")
            return []

    @staticmethod
    def extract_json_from_markdown(markdown_text):
        """Extract JSON from Markdown-formatted string."""
        json_match = re.search(r'json\s*(\[\s*.+\s*\])\s*', markdown_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
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
        """Search a topic and format the response."""
        try:
            today_prompt = f"Today is {datetime.date.today().strftime('%a, %b %e, %Y')}."
            search_prompt = (
                f"{today_prompt}\nPrepare for this prompt: {topic}\n\n"
                "What 3 Internet search topics would help you answer this question? "
                "Answer in a JSON list only."
            )
            response = prompt_llm(search_prompt)
            print(response)
            searches = self.extract_json_from_markdown(response)
            print(searches)
            for s in searches:
                print(self.scrape_google(s))

        except Exception as e:
            logging.error(f"Error during topic search: {e}")

# Example usage
if __name__ == "__main__":
    searcher = WebSearch()
    web_response = searcher.search_topic(topic= "How much has Arsenal spent on signings under Arteta?")
    print(web_response)