import logging
from llm_service import prompt_llm, summarize  # Import the LLM service
import re
import json
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from markdownify import MarkdownConverter
import requests
import datetime
import textwrap
import time
import random
import asyncio
import aiohttp
from typing import List, Tuple, Optional, Dict, Any
from cachetools import TTLCache
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

class WebSearch:
    """Perform DuckDuckGo searches and fetch web content."""

    def __init__(self, verbose: bool = False, retry_limit: int = 3, cache_maxsize: int = 128, cache_ttl: int = 300, proxies: List[str] = []):
        self.verbose = verbose
        self.retry_limit = retry_limit
        self.cache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        self.proxies = proxies
        self.session = self.create_session()
        self.executor = ThreadPoolExecutor(max_workers=10)

    def create_session(self):
        """Create a requests session with retry logic."""
        session = requests.Session()
        retry = Retry(
            total=self.retry_limit,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    async def fetch_async(self, url: str) -> Optional[str]:
        """Fetch a URL asynchronously with retry logic and exponential backoff."""
        p = random.choice(proxies)

        # Use the chosen proxy for both http and https
        proxy = {
            'http': p,
            'https': p
        }
        headers = {'User-Agent': 'Mozilla/5.0'}
        for attempt in range(self.retry_limit):
            try:
                if self.verbose:
                    logging.info(f"Attempting to fetch URL: {url} (Attempt {attempt + 1})")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, proxy=proxy, timeout=10) as response:
                        if response.status == 200:
                            return await response.text()
                        logging.warning(f"Failed to fetch {url}: HTTP {response.status}")
            except aiohttp.ClientError as e:
                logging.warning(f"Error fetching {url}: {e}")
            logging.info(f"Retrying ({attempt + 1}/{self.retry_limit})...")
            await asyncio.sleep(2 ** attempt + random.uniform(0, 1))  # Exponential backoff with jitter
        logging.error(f"Failed to fetch URL after {self.retry_limit} attempts: {url}")
        return None

    def fetch(self, url: str) -> Optional[str]:
        """Fetch a URL with retry logic and exponential backoff."""
        return asyncio.run(self.fetch_async(url))

    def ddg_search(self, topic: str) -> List[Dict[str, Any]]:
        """Search DuckDuckGo for a topic with rate limiting handling."""
        try:
            if self.verbose:
                logging.info(f"Searching DuckDuckGo for: {topic}")
            results = DDGS().text(topic)
            if '202 Ratelimit' in results:
                logging.warning(f"Rate limited by DuckDuckGo for topic: {topic}")
                time.sleep(10)  # Add a delay to handle rate limiting
                results = DDGS().text(topic)
            return results
        except Exception as e:
            logging.error(f"Error during DuckDuckGo search for '{topic}': {e}")
            return []

    def ddg_top_hit(self, topic: str, skip: List[str] = []) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Search DuckDuckGo for a topic and return the top hit."""
        results = self.ddg_search(topic)
        logging.info(f"Search results for '{topic}': {results}")
        for result in results:
            if result.get('href') in skip:
                continue
            html = self.fetch(result.get('href'))
            if html:
                title = self.extract_title(html)
                content = self.simplify_html(html)
                logging.info(f"Fetched content for '{topic}': {content}")
                if content:
                    return result['href'], title, content
        return None, None, None

    @staticmethod
    def extract_title(html: str) -> str:
        """Extract the title from an HTML document."""
        soup = BeautifulSoup(html, 'html.parser')
        return soup.title.string.strip() if soup.title else "Untitled"

    @staticmethod
    def simplify_html(html: str) -> str:
        """Convert HTML to markdown, removing some tags and links."""
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        for tag in soup.find_all("a"):
            del tag["href"]
        for tag in soup.find_all("img"):
            del tag["src"]
        text = MarkdownConverter().convert_soup(soup)
        return re.sub(r"\n(\s*\n)+", "\n\n", text)

    @staticmethod
    def extract_json_from_markdown(markdown_text: str) -> Optional[List[str]]:
        """Extract JSON from Markdown-formatted string."""
        json_match = re.search(r'json\s*(\[\s*.+\s*\])\s*', markdown_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON: {e}")
        logging.warning("No JSON found in Markdown.")
        return None

    def fetch_sources(self, search_prompt: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Fetch sources for a question with caching."""
        try:
            if search_prompt in self.cache:
                return self.cache[search_prompt]

            search_text = prompt_llm(search_prompt)
            logging.info(f"LLM search text: {search_text}")
            searches = self.extract_json_from_markdown(search_text)
            logging.info(f"Extracted searches: {searches}")
            if not searches:
                logging.warning("No search topics generated by LLM.")
                return "", []
            background_text, sources = "", []
            for search in searches:
                source, title, content = self.ddg_top_hit(search, skip=[s[0] for s in sources])
                if source:
                    background_text += f"# {search}\n\n{content}\n\n"
                    sources.append((source, title))
            self.cache[search_prompt] = (background_text, sources)
            return background_text, sources
        except Exception as e:
            logging.error(f"Error during source fetching: {e}")
            return "", []

    def format_answer(self, answer: str, sources: List[Tuple[str, str]]) -> str:
        """Format the answer with sources."""
        paragraphs = answer.splitlines()
        wrapped_paragraphs = [textwrap.fill(p, width=80) for p in paragraphs if p.strip()]
        sources_str = "\nSources:" + "\n".join([f"* [{title}]({url})" for url, title in sources])
        return "\n".join(wrapped_paragraphs) + "\n\n" + sources_str

    def search_topic(self, topic: str) -> Optional[str]:
        """Search a topic and format the response."""
        try:
            today_prompt = f"Today is {datetime.date.today().strftime('%a, %b %e, %Y')}."
            search_prompt = (
                f"{today_prompt}\nPrepare for this prompt: {topic}\n\n"
                "What 3 Internet search topics would help you answer this question? "
                "Answer in a JSON list only."
            )
            background_text, sources = self.fetch_sources(search_prompt)
            logging.info(f"Background text: {background_text}")
            logging.info(f"Sources: {sources}")
            summarized_text = summarize(background_text)
            logging.info(f"Summarized text: {summarized_text}")
            answer = prompt_llm("\n".join([today_prompt, summarized_text, f"# Prompt\n{topic}"]))
            logging.info(f"LLM answer: {answer}")
            formatted_answer = self.format_answer(answer, sources)
            logging.info("Final Answer:\n" + formatted_answer)
            return formatted_answer  # Ensure the answer is returned
        except Exception as e:
            logging.error(f"Error during topic search: {e}")
            return None

# Example usage
if __name__ == "__main__":
    proxies = [
    'https://3.97.167.115:3128',
    'https://3.97.176.251:3128' ,
    'https://15.156.24.206:3128' 
    ]


    searcher = WebSearch(proxies=proxies)
    ans = searcher.search_topic(topic="How does Tariff work?")
    print(ans)
