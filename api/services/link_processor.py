from typing import Dict, Any, List, Tuple
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re
import json
from datetime import datetime

async def process_link(url: str) -> Tuple[str, List[Dict[str, Any]], str]:
    """
    Process a link and return data in a format compatible with the file processing system.
    
    Returns:
        Tuple containing:
        - file_type: String indicating the type of link
        - extracted_data: List of dictionaries containing segments and chunks
        - file_url: The original URL
    """
    link_type = detect_link_type(url)
    metadata = await fetch_link_metadata(url, link_type)
    
    # Create segments from the metadata
    segments = []
    
    # Main content segment
    if metadata.get('title') or metadata.get('description'):
        main_content = {
            'page_number': 1,
            'content': f"Title: {metadata.get('title', 'Untitled')}\n\nDescription: {metadata.get('description', '')}",
            'meta_data': {
                'type': 'main_content',
                'url': url,
                'source': link_type
            }
        }
        segments.append(main_content)
    
    # Additional metadata segment
    if metadata.get('metadata'):
        meta_segment = {
            'page_number': 2,
            'content': json.dumps(metadata['metadata'], indent=2),
            'meta_data': {
                'type': 'metadata',
                'url': url,
                'source': link_type
            }
        }
        segments.append(meta_segment)
    
    # Create chunks for each segment
    extracted_data = []
    for segment in segments:
        # Split content into smaller chunks (e.g., by paragraphs or sentences)
        chunks = split_into_chunks(segment['content'])
        for chunk_text in chunks:
            chunk_data = {
                'segment': segment,
                'chunk_text': chunk_text,
                'embedding': None  # Will be computed by the embedding service
            }
            extracted_data.append(chunk_data)
    
    return link_type, extracted_data, url

def detect_link_type(url: str) -> str:
    """Detect the type of link based on the URL."""
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

async def fetch_link_metadata(url: str, link_type: str) -> Dict[str, Any]:
    """Fetch metadata for a link based on its type."""
    try:
        async with aiohttp.ClientSession() as session:
            if link_type == 'youtube':
                return await fetch_youtube_metadata(url, session)
            elif link_type == 'reddit':
                return await fetch_reddit_metadata(url, session)
            elif link_type == 'instagram':
                return await fetch_instagram_metadata(url, session)
            elif link_type == 'twitter':
                return await fetch_twitter_metadata(url, session)
            elif link_type == 'linkedin':
                return await fetch_linkedin_metadata(url, session)
            else:
                return await fetch_webpage_metadata(url, session)
    except Exception as e:
        print(f"Error fetching metadata for {url}: {str(e)}")
        return {'title': url, 'description': '', 'metadata': {}}

async def fetch_youtube_metadata(url: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Extract metadata from YouTube URLs."""
    video_id = extract_youtube_id(url)
    if not video_id:
        return {}

    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    
    async with session.get(oembed_url) as response:
        if response.status == 200:
            data = await response.json()
            return {
                'title': data.get('title'),
                'description': f"Video by {data.get('author_name')}",
                'metadata': {
                    'author': data.get('author_name'),
                    'thumbnail_url': data.get('thumbnail_url'),
                    'video_id': video_id,
                    'type': 'youtube_video'
                }
            }
    return {}

async def fetch_reddit_metadata(url: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Extract metadata from Reddit URLs."""
    json_url = url.rstrip('/') + '.json'
    
    async with session.get(json_url, headers={'User-Agent': 'SynTextAI/1.0'}) as response:
        if response.status == 200:
            data = await response.json()
            if data and len(data) > 0:
                post_data = data[0]['data']['children'][0]['data']
                return {
                    'title': post_data.get('title'),
                    'description': post_data.get('selftext', '')[:500],
                    'metadata': {
                        'author': post_data.get('author'),
                        'score': post_data.get('score'),
                        'num_comments': post_data.get('num_comments'),
                        'subreddit': post_data.get('subreddit'),
                        'created_utc': post_data.get('created_utc'),
                        'type': 'reddit_post'
                    }
                }
    return {}

async def fetch_webpage_metadata(url: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Extract metadata from any webpage using Open Graph tags."""
    async with session.get(url) as response:
        if response.status == 200:
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            meta_data = {}
            for tag in soup.find_all('meta', property=True):
                prop = tag.get('property', '')
                if prop.startswith('og:'):
                    meta_data[prop[3:]] = tag.get('content', '')
            
            title = meta_data.get('title') or soup.find('title')
            title = title.text if hasattr(title, 'text') else str(title)
            
            description = meta_data.get('description') or ''
            if not description:
                desc_tag = soup.find('meta', {'name': 'description'})
                if desc_tag:
                    description = desc_tag.get('content', '')
            
            return {
                'title': title,
                'description': description[:500],
                'metadata': meta_data
            }
    return {}

def extract_youtube_id(url: str) -> str:
    """Extract YouTube video ID from various YouTube URL formats."""
    patterns = [
        r'^https?:\/\/(?:www\.)?youtube\.com\/watch\?v=([^&]+)',
        r'^https?:\/\/(?:www\.)?youtube\.com\/embed\/([^?]+)',
        r'^https?:\/\/youtu\.be\/([^?]+)'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return match.group(1)
    return ''

def split_into_chunks(text: str, chunk_size: int = 1000) -> List[str]:
    """Split text into chunks of approximately equal size."""
    # First try to split by double newlines (paragraphs)
    paragraphs = text.split('\n\n')
    
    chunks = []
    current_chunk = ''
    
    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) <= chunk_size:
            current_chunk += paragraph + '\n\n'
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = paragraph + '\n\n'
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks if chunks else [text]
