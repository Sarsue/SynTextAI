"""
Transcription service for SynTextAI.

This module provides functionality for transcribing audio files with support for
multiple languages and word-level timing information.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from pydantic import ValidationError

from ..models.transcription import (
    TranscriptSegment,
    TranscriptionInfo,
    WordSegment
)
from ..core.config import settings

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Service for handling audio transcription operations."""
    
    def __init__(self, timeout_seconds: int = 300):
        """Initialize the transcription service.
        
        Args:
            timeout_seconds: Maximum time to wait for transcription to complete
        """
        self.timeout_seconds = timeout_seconds
    
    async def transcribe_audio_chunked(
        self,
        file_path: str,
        language: str = "en",
        chunk_duration_ms: int = 30000,
        overlap_ms: int = 5000,
    ) -> Tuple[List[TranscriptSegment], TranscriptionInfo]:
        """Transcribe audio using the MCP service with enhanced error handling.
        
        Args:
            file_path: Path to the audio file to transcribe
            language: ISO 639-1 language code (e.g., 'en' for English)
            chunk_duration_ms: Duration of each audio chunk in milliseconds
            overlap_ms: Overlap between chunks in milliseconds
            
        Returns:
            Tuple of (segments, metadata) where:
                - segments: List of transcript segments with word-level timestamps
                - metadata: Transcription metadata and language information
                
        Raises:
            FileNotFoundError: If the input file doesn't exist or is not accessible
            ValueError: If the language code is invalid or the audio file is empty/corrupt
            asyncio.TimeoutError: If the transcription takes too long
            Exception: For any errors during the transcription process
        """
        start_time = time.time()
        
        try:
            # Input validation
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Audio file not found: {file_path}")
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                raise ValueError(f"Audio file is empty: {file_path}")
                
            if language and (not isinstance(language, str) or len(language.strip()) != 2):
                raise ValueError(
                    "Language must be a valid ISO 639-1 language code (e.g., 'en', 'es')"
                )
            
            logger.info(
                "Starting transcription of %s (size: %.2fMB) with language: %s",
                file_path,
                file_size / (1024 * 1024),
                language or "auto"
            )
            
            # TODO: Implement actual transcription logic using MCP service
            # For now, return a placeholder response
            segments = [
                TranscriptSegment(
                    start=0.0,
                    end=1.0,
                    text="Sample transcription",
                    words=[
                        WordSegment(
                            word="sample",
                            start=0.0,
                            end=0.5,
                            probability=0.95
                        ),
                        WordSegment(
                            word="transcription",
                            start=0.5,
                            end=1.0,
                            probability=0.92
                        )
                    ]
                )
            ]
            
            metadata = TranscriptionInfo(
                language=language or "en",
                language_probability=1.0,
                duration=1.0,
                file_size=file_size,
                segment_count=len(segments),
                processing_time=time.time() - start_time
            )
            
            return segments, metadata
            
        except Exception as e:
            logger.error("Transcription failed: %s", str(e), exc_info=True)
            raise
    
    async def adapt_whisper_segments_to_transcript_data(
        self,
        whisper_segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert Whisper segments to the format expected by downstream processing.
        
        Args:
            whisper_segments: List of segment dictionaries from the transcription service.
                Each segment should contain at least 'start', 'end', and 'text' keys,
                and optionally a 'words' list with word-level timing information.
                
        Returns:
            List of segments in the standardized format.
            
        Raises:
            TypeError: If whisper_segments is not a list or contains invalid items
            ValueError: If required fields are missing or have invalid types
        """
        if not isinstance(whisper_segments, list):
            raise TypeError(f"Expected list of segments, got {type(whisper_segments).__name__}")
        
        result = []
        
        for i, segment in enumerate(whisper_segments):
            try:
                # Extract word segments if available
                word_segments = []
                if 'words' in segment and isinstance(segment['words'], list):
                    for word in segment['words']:
                        word_segments.append({
                            'word': str(word.get('word', '')),
                            'start': float(word.get('start', 0.0)),
                            'end': float(word.get('end', 0.0)),
                            'probability': float(word.get('probability', 1.0))
                        })
                
                # Create the segment dictionary
                result.append({
                    'start': float(segment.get('start', 0.0)),
                    'end': float(segment.get('end', 0.0)),
                    'text': str(segment.get('text', '')).strip(),
                    'words': word_segments
                })
                
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(
                    "Skipping invalid segment %d: %s",
                    i,
                    str(e),
                    exc_info=True
                )
                continue
        
        return result

# Create a singleton instance of the service
transcription_service = TranscriptionService()
