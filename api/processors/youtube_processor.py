"""
Production-ready YouTube processor.
Handles yt-dlp download, FFmpeg normalization, Whisper transcription,
chunked embeddings, and concept extraction with safe cleanup.
"""

import asyncio
import functools
import logging
import os
import re
import tempfile
import shutil
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import torch
import whisper
import yt_dlp
from aiohttp import ClientSession

from whisper.audio import SAMPLE_RATE

from api.processors.base_processor import FileProcessor
from api.repositories import RepositoryManager, get_repository_manager
from api.services.embedding_service import embedding_service
from api.services.llm_service import llm_service
from api.core.config import settings

logger = logging.getLogger(__name__)

_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_EMBED_CONCURRENCY = 5
_CONCEPT_CONCURRENCY = 5

@functools.lru_cache(maxsize=1)
def get_whisper_model(model_name: str = "base") -> whisper.Whisper:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return whisper.load_model(model_name).to(device)

def get_database_url() -> str:
    from api.core.config import settings
    return str(settings.DATABASE_URL)

def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:youtube\.com/(?:[^/]+/.+/|(?:v|e(?:mbed)?)/|.*[?&]v=)|youtu\.be/)([^\"&?/\\s]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


class YouTubeProcessor(FileProcessor):
    def __init__(self, store: RepositoryManager, model_name: str = "base"):
        super().__init__()
        self.store = store
        self.model = get_whisper_model(model_name)
        self.sample_rate = SAMPLE_RATE
        self.session: Optional[ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _get_metadata(self, video_id: str) -> Dict[str, Any]:
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            async with self.session.get(url) as resp:
                return await resp.json() if resp.status == 200 else {}
        except Exception as e:
            logger.warning(f"Metadata fetch failed: {e}")
            return {}

    async def _download_and_normalize(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        temp_dir = tempfile.mkdtemp(prefix="yt_audio_")
        raw_out = os.path.join(temp_dir, "audio.%(ext)s")

        opts = {
            "format": "bestaudio/best",
            "outtmpl": raw_out,
            "quiet": True,
            "no_warnings": True,
        }

        try:
            info = await asyncio.get_event_loop().run_in_executor(
                _CPU_EXECUTOR, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=True)
            )

            dl_file = None
            for ext in (".m4a", ".webm", ".mp3", ".wav"):
                candidate = os.path.join(temp_dir, f"audio{ext}")
                if os.path.exists(candidate):
                    dl_file = candidate
                    break
            if not dl_file:
                raise RuntimeError("yt-dlp did not produce an audio file")

            wav_file = os.path.join(temp_dir, "audio.wav")
            cmd = [
                "ffmpeg", "-y", "-i", dl_file,
                "-ar", str(self.sample_rate), "-ac", "1",
                "-acodec", "pcm_s16le", wav_file
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {err.decode()}")

            return wav_file, temp_dir, None

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None, None, str(e)

    async def _transcribe(self, wav_file: str, language: str) -> Dict[str, Any]:
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                _CPU_EXECUTOR, lambda: self.model.transcribe(wav_file, language=language)
            )
            segments = [
                {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                for s in result.get("segments", [])
            ]
            return {"success": True, "text": result.get("text", ""), "segments": segments}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def extract_content(self, file_data: str, file_id: int, user_id: int, filename: str = "", language: str = "en") -> Dict[str, Any]:
        video_id = extract_video_id(file_data)
        if not video_id:
            return {"success": False, "error": "Invalid YouTube URL"}

        temp_dir = None
        try:
            metadata = await self._get_metadata(video_id)
            wav_file, temp_dir, error = await self._download_and_normalize(file_data)
            if error:
                return {"success": False, "error": f"Download/convert failed: {error}"}

            result = await self._transcribe(wav_file, language)
            if not result["success"]:
                return result

            return {
                "success": True,
                "video_id": video_id,
                "transcript": result["text"],
                "segments": result["segments"],
                "metadata": {
                    **metadata,
                    "filename": filename or metadata.get("title", f"youtube_{video_id}"),
                    "url": file_data,
                    "file_id": file_id,
                    "user_id": user_id,
                    "source_type": "youtube",
                    "thumbnail_url": metadata.get("thumbnail_url"),
                    "author": metadata.get("author_name"),
                    "processing_status": "completed",
                },
            }

        except Exception as e:
            logger.error(f"extract_content failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _split_text_with_overlap(self, segments: List[Dict[str, Any]], chunk_size: int = 1000, chunk_overlap: int = 200) -> List[Dict[str, Any]]:
        chunks, buffer, length, start_time = [], [], 0, None
        for seg in segments:
            seg_text = seg["text"]
            seg_len = len(seg_text)
            if not buffer:
                start_time = seg["start"]
            if length + seg_len > chunk_size:
                chunk_text = " ".join(buffer)
                chunks.append({"text": chunk_text, "start": start_time, "end": seg["start"]})
                overlap_tokens = " ".join(buffer[-(chunk_overlap // 5):])
                buffer = [overlap_tokens] if overlap_tokens else []
                length = len(overlap_tokens)
                start_time = seg["start"]
            buffer.append(seg_text)
            length += seg_len + 1
        if buffer:
            chunk_text = " ".join(buffer)
            chunks.append({"text": chunk_text, "start": start_time, "end": segments[-1]["end"]})
        return chunks

    async def _batch_embed_texts(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        semaphore = asyncio.Semaphore(_EMBED_CONCURRENCY)
        all_embeddings = []

        async def embed(text: str):
            async with semaphore:
                try:
                    return await embedding_service.get_embedding(text)
                except Exception as e:
                    logger.error(f"Embedding failed: {e}")
                    return [0.0] * 1536

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            results = await asyncio.gather(*[embed(t) for t in batch])
            all_embeddings.extend(results)

        return all_embeddings

    async def generate_embeddings(self, content: Dict[str, Any], chunk_size: int = 1000, chunk_overlap: int = 200) -> Dict[str, Any]:
        segments = content.get("segments", [])
        if not segments:
            return {"success": False, "error": "No transcript segments to embed"}

        chunks = self._split_text_with_overlap(segments, chunk_size, chunk_overlap)
        texts = [c["text"] for c in chunks]
        embeddings = await self._batch_embed_texts(texts)

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk.update({
                "embedding": emb,
                "chunk_index": i,
                "token_count": len(chunk["text"].split()),
            })

        return {"success": True, "processed_segments": chunks}

    async def generate_key_concepts(self, content: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunks = content.get("processed_segments", [])
        semaphore = asyncio.Semaphore(_CONCEPT_CONCURRENCY)

        async def extract_concepts(chunk: Dict[str, Any]):
            async with semaphore:
                try:
                    concepts = await llm_service.generate_key_concepts_dspy(chunk["text"])
                    for c in concepts:
                        c.update({"start": chunk["start"], "end": chunk["end"], "chunk_index": chunk["chunk_index"]})
                    return concepts
                except Exception as e:
                    logger.error(f"Concept extraction failed: {e}")
                    return []

        results = await asyncio.gather(*[extract_concepts(c) for c in chunks])
        all_concepts = [c for r in results for c in r]
        return sorted(all_concepts, key=lambda x: x.get("relevance", 0), reverse=True)


async def process_youtube(url: str, file_id: int, user_id: int, filename: str = "", language: str = "en"):
    """Process a YouTube URL and return structured data.
    
    Args:
        url: YouTube URL or video ID
        file_id: Database file ID
        user_id: User ID
        filename: Optional filename
        language: Language for transcription (default: "en")
        
    Returns:
        Dict containing processed video data
    """
    from api.repositories import get_repository_manager
    
    repo_manager = await get_repository_manager()
    processor = YouTubeProcessor(repo_manager)
    
    async with processor:
        return await processor.extract_content(
            file_data=url,
            file_id=file_id,
            user_id=user_id,
            filename=filename or url,
            language=language
        )
