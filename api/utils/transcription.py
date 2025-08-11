"""
Utility functions for handling transcription data.

This module provides helper functions for processing and transforming
transcription data between different formats.
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

def format_transcript_as_text(segments: List[Dict[str, Any]]) -> str:
    """Convert transcript segments into plain text.
    
    Args:
        segments: List of transcript segments with 'text' field
        
    Returns:
        Plain text representation of the transcript
    """
    if not segments:
        return ""
    return "\n".join(segment.get('text', '').strip() for segment in segments)

def filter_transcript_by_time_range(
    segments: List[Dict[str, Any]],
    start_time: float,
    end_time: float
) -> List[Dict[str, Any]]:
    """Filter transcript segments by time range.
    
    Args:
        segments: List of transcript segments
        start_time: Start time in seconds
        end_time: End time in seconds
        
    Returns:
        Filtered list of segments that fall within the specified time range
    """
    return [
        segment for segment in segments
        if (segment.get('start', 0) >= start_time and 
            segment.get('end', 0) <= end_time)
    ]

def merge_adjacent_segments(
    segments: List[Dict[str, Any]],
    max_gap: float = 0.5
) -> List[Dict[str, Any]]:
    """Merge adjacent segments that are close to each other.
    
    Args:
        segments: List of transcript segments
        max_gap: Maximum gap in seconds between segments to consider them adjacent
        
    Returns:
        List of merged segments
    """
    if not segments:
        return []
        
    result = [segments[0]]
    
    for current in segments[1:]:
        last = result[-1]
        
        # If segments are close enough, merge them
        if (current['start'] - last['end']) <= max_gap:
            merged = {
                'start': last['start'],
                'end': current['end'],
                'text': f"{last['text']} {current['text']}".strip(),
                'words': last.get('words', []) + current.get('words', [])
            }
            result[-1] = merged
        else:
            result.append(current)
    
    return result

def calculate_speaking_rate(segments: List[Dict[str, Any]]) -> float:
    """Calculate the speaking rate in words per minute.
    
    Args:
        segments: List of transcript segments with word timing information
        
    Returns:
        Speaking rate in words per minute
    """
    if not segments:
        return 0.0
    
    total_words = 0
    total_duration = 0.0
    
    for segment in segments:
        words = segment.get('words', [])
        if not words:
            continue
            
        total_words += len(words)
        total_duration = max(total_duration, segment.get('end', 0.0))
    
    if total_duration <= 0:
        return 0.0
        
    return (total_words / total_duration) * 60.0
