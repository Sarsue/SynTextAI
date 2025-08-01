"""
Web Search Service

This module provides web search functionality using multiple search providers
(SearxNG, Tavily) with result aggregation and content extraction.
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Configuration
SEARXNG_URL = os.getenv("SEARXNG_URL")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 15  # seconds

class WebSearchService:
    """Service for performing web searches and processing results."""
    
    def __init__(self):
        """Initialize the web search service with configured providers."""
        self.providers = self._initialize_providers()
        logger.info(f"Initialized WebSearchService with providers: {list(self.providers.keys())}")
    
    def _initialize_providers(self) -> Dict[str, callable]:
        """Initialize available search providers."""
        providers = {}
        
        if SEARXNG_URL:
            providers['searxng'] = self._search_searxng
        
        if TAVILY_API_KEY:
            providers['tavily'] = self._search_tavily
            
        return providers
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def search(
        self, 
        query: str, 
        max_results: int = 5,
        max_tokens_per_source: int = 800,
        timeout: int = DEFAULT_TIMEOUT
    ) -> List[Dict[str, Any]]:
        """
        Search the web using available providers and return aggregated results.
        
        Args:
            query: The search query
            max_results: Maximum number of results to return
            max_tokens_per_source: Maximum tokens to keep per source
            timeout: Request timeout in seconds
            
        Returns:
            List of search results with content and metadata
        """
        if not self.providers:
            logger.warning("No search providers configured")
            return []
        
        # Try providers in order until we get results
        for provider_name, search_func in self.providers.items():
            try:
                logger.info(f"Trying search provider: {provider_name}")
                results = await search_func(query, max_results, timeout)
                if results:
                    # Process and return the first set of valid results
                    return self._process_results(results, max_tokens_per_source)
            except Exception as e:
                logger.error(f"Error with {provider_name} search: {e}", exc_info=True)
                continue
                
        return []
    
    async def _search_tavily(
        self, 
        query: str, 
        max_results: int,
        timeout: int
    ) -> List[Dict[str, Any]]:
        """Search using Tavily API."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TAVILY_API_KEY}"
        }
        data = {
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": True
        }
        
        response = requests.post(
            TAVILY_URL, 
            headers=headers, 
            json=data,
            timeout=timeout
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            # Include answer if available
            if 'answer' in data and data['answer']:
                results.append({
                    'title': 'Answer',
                    'content': data['answer'],
                    'url': data.get('follow_up_questions', [{}])[0].get('url', '')
                })
            
            # Include search results
            for result in data.get('results', [])[:max_results]:
                results.append({
                    'title': result.get('title', ''),
                    'content': result.get('content', ''),
                    'url': result.get('url', ''),
                    'score': result.get('score', 0)
                })
            
            return results
            
        return []
    
    async def _search_searxng(
        self, 
        query: str, 
        max_results: int,
        timeout: int
    ) -> List[Dict[str, Any]]:
        """Search using SearxNG instance."""
        params = {
            "q": query, 
            "format": "json",
            "language": "en",
            "safesearch": 1,
            "pageno": 1,
            "time_range": None  # Can be 'day', 'week', 'month', 'year'
        }
        
        response = requests.get(SEARXNG_URL, params=params, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            for result in data.get('results', [])[:max_results]:
                results.append({
                    'title': result.get('title', ''),
                    'content': result.get('content', ''),
                    'url': result.get('url', ''),
                    'score': result.get('score', 0)
                })
            
            return results
            
        return []
    
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
    async def fetch_page_content(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
        """
        Fetch and clean content from a web page.
        
        Args:
            url: URL to fetch content from
            timeout: Request timeout in seconds
            
        Returns:
            Cleaned text content or None if failed
        """
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.decompose()
                
                # Remove href attributes from <a> tags but keep the text
                for a in soup.find_all("a"):
                    a.replace_with(a.get_text())

                # Extract text
                text = soup.get_text(separator=" ")

                # Clean up whitespace and newlines
                cleaned_text = re.sub(r'\s+', ' ', text).strip()
                return cleaned_text
                
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            
        return None
    
    def _process_results(
        self, 
        results: List[Dict[str, Any]], 
        max_tokens: int = 800
    ) -> List[Dict[str, Any]]:
        """
        Process and clean search results.
        
        Args:
            results: Raw search results
            max_tokens: Maximum tokens to keep per result
            
        Returns:
            Processed results with cleaned content
        """
        processed = []
        
        for result in results:
            if not result.get('content'):
                continue
                
            # Clean and truncate content
            content = result['content']
            if max_tokens and len(content.split()) > max_tokens:
                content = ' '.join(content.split()[:max_tokens]) + '...'
            
            processed.append({
                'title': result.get('title', 'Untitled'),
                'content': content,
                'url': result.get('url', ''),
                'score': result.get('score', 0)
            })
        
        # Sort by score (highest first) and remove duplicates by URL
        seen_urls = set()
        unique_results = []
        
        for result in sorted(processed, key=lambda x: x.get('score', 0), reverse=True):
            url = result.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(result)
        
        return unique_results


# Singleton instance
web_search_service = WebSearchService()
