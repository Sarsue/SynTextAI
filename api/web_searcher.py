from llm_service import prompt_llm, summarize # Import the LLM service
import re
import json
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from markdownify import MarkdownConverter
import requests
import datetime
import textwrap

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

def simplify_html(html):
    """Convert HTML to markdown, removing some tags and links."""
    soup = BeautifulSoup(html, 'html.parser')

    # Remove unwanted tags
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Remove links. They're not helpful.
    for tag in soup.find_all("a"):
        del tag["href"]
    for tag in soup.find_all("img"):
        del tag["src"]
    soup.smooth()

    # Turn HTML into markdown, preserving some formatting
    text = MarkdownConverter().convert_soup(soup)
    text = re.sub(r"\n(\s*\n)+", "\n\n", text)
    return text

def extract_title(html):
    """Extract the title from an HTML document."""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.title.string if soup.title else "Untitled"

class WebSearch:
    """Perform DuckDuckGo searches and fetch web content."""
    
    def __init__(self, verbose=False):
        self.verbose = verbose

    def fetch(self, url):
        """Fetch a URL."""
        if self.verbose:
            print(f"Fetching: {url}")
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.content
            else:
                print(f"Error fetching {url}: {response.status_code}")
                return None
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
            return None

    def ddg_search(self, topic):
        """Search DuckDuckGo for a topic."""
        if self.verbose:
            print(f"Searching DuckDuckGo for: {topic}")
        return DDGS().text(topic)

    def ddg_top_hit(self, topic, skip=()):
        """Search DuckDuckGo for a topic and return the top hit."""
        results = self.ddg_search(topic)
        for result in results:
            if result['href'] in skip:
                continue
            if self.verbose:
                print(f"Fetching content from: {result['href']}")
            html = self.fetch(result['href'])
            if html:
                title = extract_title(html)
                content = simplify_html(html)
                if content:
                    return result['href'], title, content
        return None, None, None


    def extract_json_from_markdown(self,markdown_text):
        """
        Extracts JSON from a Markdown-formatted string.
        
        Args:
            markdown_text (str): The Markdown-formatted string returned by the LLM.
        
        Returns:
            list: The parsed JSON list.
        """
        # Use a regular expression to find the JSON part
        json_match = re.search(r'```json\s*(\[\s*.+\s*\])\s*```', markdown_text, re.DOTALL)

        if json_match:
            # Extract the JSON part and remove any extra spaces
            json_str = json_match.group(1).strip()
            
            # Now load the JSON string into a Python list
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON: {e}")
                return None
        else:
            print("No JSON found in the Markdown.")
            return None

    def fetch_sources(self, search_prompt):
            """Fetch sources for a question."""
            search_text = prompt_llm(search_prompt)
            print(search_text)
            searches = self.extract_json_from_markdown(search_text)
            print(searches)
            background_text = ""
            sources = []
            for search in searches:
                source, title, content = self.ddg_top_hit(search,
                                                        skip=[source for source, _ in sources])
                if not source:
                    continue
                background_text += f"# {search}\n\n{content}\n\n"
                sources.append((source, title))
            return background_text, sources
  

    def format_answer(self, answer, sources):
        paragraphs = answer.splitlines()
        wrapped_paragraphs = [textwrap.wrap(p) for p in paragraphs]
        
        # Format wrapped paragraphs into a single string
        formatted_paragraphs = "\n".join("\n".join(p) for p in wrapped_paragraphs)
        
        # Format sources
        sources_str = "\nSources:"
        for source, title in sources:
            sources_str += f"\n* [{title}]({source})"
        
        # Return the formatted output
        return formatted_paragraphs + "\n" + sources_str


    def search_topic(self, topic):
        today_prompt = f"Today is {datetime.date.today().strftime('%a, %b %e, %Y')}."
        search_prompt = ("# Background\n\n"
                            f"{today_prompt}\n\n"
                            f"Prepare for this prompt: {topic}\n\n"
                            "# Prompt\n\n"
                            "What 3 Internet search topics would help you answer this "
                            "question? Answer in a JSON list only.")
        background_text, sources = self.fetch_sources(search_prompt)
        background_text = summarize(background_text,
                    prompt=f"{today_prompt}\n\n"
                    "You provide helpful and complete answers.\n\n"
                    f"Make a list of facts that would help with: {topic}\n\n")
      
        answer = prompt_llm("\n\n".join([
                "# Background",
                background_text,
                today_prompt,
                "You provide helpful and complete answers.",
                "# Prompt",
                f"{topic}"]))
        ans = self.format_answer(answer,sources)
        print(ans)

   


# Example usage
if __name__ == "__main__":
    searcher = WebSearch()
    searcher.search_topic(topic= "How much did perplexity ai make?")
   