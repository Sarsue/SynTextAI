"""
Test script for YouTube processor.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.repositories import RepositoryManager
from api.processors.youtube_processor import YouTubeProcessor, process_youtube

# Test YouTube video ID (a short, public domain video)
TEST_VIDEO_ID = "dQw4w9WgXcQ"  # YouTube's "This video is unavailable" placeholder

def mock_youtube_transcript(video_id, languages=None):
    """Mock YouTube transcript API response."""
    if video_id == TEST_VIDEO_ID:
        return [
            {
                'text': 'This is a test transcript.',
                'start': 0.0,
                'duration': 5.0
            },
            {
                'text': 'It contains multiple sentences.',
                'start': 5.0,
                'duration': 5.0
            },
            {
                'text': 'This is the final sentence.',
                'start': 10.0,
                'duration': 5.0
            }
        ]
    return None

# Patch the YouTubeTranscriptApi if available
try:
    from unittest.mock import patch
    patch_target = 'api.processors.youtube_processor.YouTubeTranscriptApi.get_transcript'
    patch_context = patch(patch_target, side_effect=mock_youtube_transcript)
    patch_context.start()
except ImportError:
    patch_context = None

async def test_youtube_processor():
    """Test the YouTube processor with a sample video."""
    # Initialize repository manager
    repo_manager = RepositoryManager()
    
    # Test the standalone function
    print("Testing process_youtube function...")
    result = await process_youtube(
        video_url=f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}",
        file_id=1,
        user_id=1,
        filename="test_video"
    )
    
    print("\nProcess YouTube Result:")
    print(f"Success: {result.get('success')}")
    print(f"Error: {result.get('error')}")
    print(f"Video ID: {result.get('video_id')}")
    print(f"Transcript Length: {len(result.get('transcript', ''))} characters")
    print(f"Segments: {len(result.get('segments', []))} found")
    print(f"Key Concepts: {len(result.get('key_concepts', []))} found")
    
    # Test the processor directly
    print("\nTesting YouTubeProcessor class...")
    processor = YouTubeProcessor(repo_manager)
    
    # Test extract_content
    print("\nTesting extract_content...")
    content = await processor.extract_content(
        file_data=f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}",
        file_id=1,
        user_id=1,
        filename="test_video"
    )
    print(f"Extraction Success: {content.get('success')}")
    print(f"Segments: {len(content.get('segments', []))}")
    
    if not content.get('success'):
        print(f"Error: {content.get('error')}")
        return
    
    # Test generate_embeddings
    print("\nTesting generate_embeddings...")
    embeddings = await processor.generate_embeddings(content)
    print(f"Embeddings Success: embeddings.get('success')")
    print(f"Processed Segments: len(embeddings.get('processed_segments', [])) if embeddings.get('success') else 'N/A')")
    
    if not embeddings.get('success'):
        print(f"Error: {embeddings.get('error')}")
        return
    
    # Test generate_key_concepts
    print("\nTesting generate_key_concepts...")
    key_concepts = await processor.generate_key_concepts(embeddings)
    print(f"Key Concepts: {len(key_concepts)} found")
    
    if key_concepts:
        print("\nSample Key Concept:")
        print(f"Title: {key_concepts[0].get('concept_title')}")
        print(f"Explanation: {key_concepts[0].get('concept_explanation', '')[:100]}...")

if __name__ == "__main__":
    try:
        asyncio.run(test_youtube_processor())
    finally:
        # Clean up patches
        if patch_context:
            patch_context.stop()
