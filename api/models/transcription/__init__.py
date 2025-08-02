"""
Transcription-related data models for SynTextAI.

This module contains Pydantic models for representing transcription data,
including word-level timing information and transcription metadata.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

__all__ = [
    'WordSegment',
    'TranscriptSegment',
    'TranscriptionInfo'
]

class WordSegment(BaseModel):
    """Represents a single word in the transcription with timing information.
    
    Attributes:
        word: The text of the word
        start: Start time in seconds
        end: End time in seconds
        probability: Confidence score (0.0 to 1.0)
    """
    word: str
    start: float
    end: float
    probability: float = Field(..., ge=0.0, le=1.0)

class TranscriptSegment(BaseModel):
    """Represents a segment of transcribed text with timing and word-level details.
    
    Attributes:
        start: Start time of the segment in seconds
        end: End time of the segment in seconds
        text: The transcribed text of the segment
        words: List of word-level details if available
    """
    start: float
    end: float
    text: str
    words: List[WordSegment] = Field(default_factory=list)

class TranscriptionInfo(BaseModel):
    """Metadata about the transcription process and results.
    
    Attributes:
        language: Detected language code (e.g., 'en', 'es')
        language_probability: Confidence score for language detection (0.0 to 1.0)
        duration: Total duration of the audio in seconds
        all_language_probs: Probability distribution for all detected languages
        error: Error message if transcription failed
        processing_time: Time taken to process the transcription
        file_size: Size of the processed file in bytes
        segment_count: Number of segments in the transcription
    """
    language: str
    language_probability: float = Field(..., ge=0.0, le=1.0)
    duration: float
    all_language_probs: Optional[Dict[str, float]] = None
    error: Optional[str] = None
    processing_time: Optional[float] = None
    file_size: Optional[int] = None
    segment_count: Optional[int] = None
