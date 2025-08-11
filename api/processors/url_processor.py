"""
URL Processor for handling various types of web URLs.

This processor handles different types of URLs including:
- Web pages
- YouTube videos
- Reddit posts
- Twitter/X posts
- Instagram posts
- LinkedIn posts
- And other web content
"""
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re
import logging
from typing import Dict, Any, List, Optional

from .base_processor import FileProcessor
from ..services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

class URLProcessor(FileProcessor):
    """Processor for handling various types of web URLs."""
    
    def __init__(self, embedding_service: Optional[EmbeddingService] = None):
        """Initialize the URL processor.
        
        Args:
            embedding_service: Optional embedding service instance
        """
        self.embedding_service = embedding_service or EmbeddingService()
        self.chunk_size = 1000  # Default chunk size for text splitting
    
    async def process(self, 
                     user_id: str, 
                     file_id: str, 
                     filename: str, 
                     file_url: str, 
                     **kwargs) -> Dict[str, Any]:
        """Process a URL and return structured data.
        
        Args:
            user_id: User ID
            file_id: File ID for tracking
            filename: Original filename (if any)
            file_url: The URL to process
            **kwargs: Additional parameters
            
        Returns:
            Dict containing processed URL data
        """
        logger.info(f"Processing URL: {file_url}")
        
        # Step 1: Detect URL type
        url_type = self.detect_url_type(file_url)
        
        # Step 2: Fetch and extract content
        content = await self.extract_content(file_url, url_type, **kwargs)
        
        # Step 3: Generate embeddings if needed
        if self.embedding_service:
            content = await self.generate_embeddings(content)
        
        # Step 4: Format the response
        return {
            'status': 'success',
            'type': url_type,
            'metadata': content.get('metadata', {}),
            'segments': content.get('segments', []),
            'chunks': content.get('chunks', [])
        }
    
    async def extract_content(self, 
                            url: str, 
                            url_type: Optional[str] = None,
                            **kwargs) -> Dict[str, Any]:
        """Extract content from a URL.
        
        Args:
            url: The URL to extract content from
            url_type: Optional pre-detected URL type
            **kwargs: Additional parameters
            
        Returns:
            Dict containing extracted content
        """
        url_type = url_type or self.detect_url_type(url)
        
        try:
            async with aiohttp.ClientSession() as session:
                if url_type == 'youtube':
                    return await self._extract_youtube_content(url, session, **kwargs)
                elif url_type == 'reddit':
                    return await self._extract_reddit_content(url, session, **kwargs)
                elif url_type == 'twitter':
                    return await self._extract_twitter_content(url, session, **kwargs)
                elif url_type == 'instagram':
                    return await self._extract_instagram_content(url, session, **kwargs)
                elif url_type == 'linkedin':
                    return await self._extract_linkedin_content(url, session, **kwargs)
                else:
                    return await self._extract_webpage_content(url, session, **kwargs)
                    
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}", exc_info=True)
            raise
    
    async def generate_embeddings(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Generate embeddings for the extracted content.
        
        Args:
            content: Dictionary containing extracted content
            
        Returns:
            Content with embeddings added
        """
        if 'chunks' not in content:
            return content
            
        for chunk in content['chunks']:
            if 'text' in chunk and 'embedding' not in chunk:
                chunk['embedding'] = await self.embedding_service.generate_embedding(chunk['text'])
                
        return content
    
    def detect_url_type(self, url: str) -> str:
        """Detect the type of URL.
        
        Args:
            url: The URL to analyze
            
        Returns:
            String indicating the URL type (e.g., 'youtube', 'reddit')
        """
        domain = urlparse(url).netloc.lower()
        
        if 'youtube.com' in domain or 'youtu.be' in domain:
            return 'youtube'
        elif 'reddit.com' in domain:
            return 'reddit'
        elif 'instagram.com' in domain:
            return 'instagram'
        elif 'twitter.com' in domain or 'x.com' in domain:
            return 'twitter'
        elif 'linkedin.com' in domain:
            return 'linkedin'
        else:
            return 'webpage'
    
    async def _extract_webpage_content(self, 
                                     url: str, 
                                     session: aiohttp.ClientSession,
                                     **kwargs) -> Dict[str, Any]:
        """Extract content from a standard webpage."""
        async with session.get(url) as response:
            html = await response.text()
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract metadata
        metadata = {
            'title': self._extract_meta_property(soup, 'og:title') or soup.title.string if soup.title else None,
            'description': self._extract_meta_property(soup, 'og:description') or \
                          self._extract_meta_property(soup, 'description'),
            'url': url,
            'type': self._extract_meta_property(soup, 'og:type') or 'website',
            'image': self._extract_meta_property(soup, 'og:image'),
            'site_name': self._extract_meta_property(soup, 'og:site_name'),
            'published_time': self._extract_meta_property(soup, 'article:published_time')
        }
        
        # Extract main content
        main_content = self._extract_main_content(soup)
        
        # Split into chunks
        chunks = self._split_into_chunks(main_content)
        
        return {
            'metadata': metadata,
            'segments': [{
                'type': 'main_content',
                'content': main_content,
                'metadata': metadata
            }],
            'chunks': [{'text': chunk, 'metadata': metadata} for chunk in chunks]
        }
    
    async def _extract_youtube_content(self, 
                                      url: str, 
                                      session: aiohttp.ClientSession,
                                      **kwargs) -> Dict[str, Any]:
        """Extract content from a YouTube URL."""
        # TODO: Implement YouTube content extraction
        video_id = self._extract_youtube_id(url)
        
        # For now, return basic metadata
        return {
            'metadata': {
                'type': 'youtube',
                'url': url,
                'video_id': video_id
            },
            'segments': [],
            'chunks': []
        }
    
    async def _extract_reddit_content(self, 
                                     url: str, 
                                     session: aiohttp.ClientSession,
                                     **kwargs) -> Dict[str, Any]:
        """Extract content from a Reddit URL."""
        # TODO: Implement Reddit content extraction
        return {
            'metadata': {
                'type': 'reddit',
                'url': url
            },
            'segments': [],
            'chunks': []
        }
    
    async def _extract_twitter_content(self, 
                                      url: str, 
                                      session: aiohttp.ClientSession,
                                      **kwargs) -> Dict[str, Any]:
        """Extract content from a Twitter/X URL."""
        # TODO: Implement Twitter content extraction
        return {
            'metadata': {
                'type': 'twitter',
                'url': url
            },
            'segments': [],
            'chunks': []
        }
    
    async def _extract_instagram_content(self, 
                                        url: str, 
                                        session: aiohttp.ClientSession,
                                        **kwargs) -> Dict[str, Any]:
        """Extract content from an Instagram URL."""
        # TODO: Implement Instagram content extraction
        return {
            'metadata': {
                'type': 'instagram',
                'url': url
            },
            'segments': [],
            'chunks': []
        }
    
    async def _extract_linkedin_content(self, 
                                       url: str, 
                                       session: aiohttp.ClientSession,
                                       **kwargs) -> Dict[str, Any]:
        """Extract content from a LinkedIn URL."""
        # TODO: Implement LinkedIn content extraction
        return {
            'metadata': {
                'type': 'linkedin',
                'url': url
            },
            'segments': [],
            'chunks': []
        }
    
    def _extract_meta_property(self, soup: BeautifulSoup, property_name: str) -> Optional[str]:
        """Extract a meta property from the HTML."""
        tag = soup.find('meta', property=property_name) or soup.find('meta', attrs={'name': property_name})
        return tag.get('content') if tag else None
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract the main content from the HTML."""
        # Try to find the main content using common selectors
        selectors = [
            'article',
            'main',
            '#main',
            '#content',
            '#article',
            '.main',
            '.content',
            '.article',
            'div[role="main"]'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                # Clean up the content
                for elem in element(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                    elem.decompose()
                return element.get_text('\n', strip=True)
        
        # Fall back to body text if no main content found
        return soup.body.get_text('\n', strip=True) if soup.body else ''
    
    def _split_into_chunks(self, text: str, chunk_size: Optional[int] = None) -> List[str]:
        """Split text into chunks of approximately equal size."""
        if not text:
            return []
            
        chunk_size = chunk_size or self.chunk_size
        words = text.split()
        chunks = []
        current_chunk = []
        current_length = 0
        
        for word in words:
            word_length = len(word) + 1  # +1 for space
            if current_length + word_length > chunk_size and current_chunk:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_length = 0
                
            current_chunk.append(word)
            current_length += word_length
            
        if current_chunk:
            chunks.append(' '.join(current_chunk))
            
        return chunks
    
    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})',
            r'youtube\.com\/watch\?v=([^&\s]+)',
            r'youtu\.be\/([^&\s]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
                
        return None
