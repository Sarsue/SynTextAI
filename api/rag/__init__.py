"""
Retrieval Augmented Generation (RAG) package for SynTextAI.
This package provides modular components for building RAG pipelines.
"""

from .interfaces import (
    QueryProcessorInterface,
    SearchEngineInterface,
    ReRankerInterface,
    ChunkSelectorInterface
)

from .query_processor import DefaultQueryProcessor
from .search_engine import HybridSearchEngine
from .reranker import CrossEncoderReRanker
from .chunk_selector import SmartChunkSelector
from .factory import RAGFactory

__all__ = [
    'QueryProcessorInterface',
    'SearchEngineInterface',
    'ReRankerInterface',
    'ChunkSelectorInterface',
    'DefaultQueryProcessor',
    'HybridSearchEngine',
    'CrossEncoderReRanker',
    'SmartChunkSelector',
    'RAGFactory'
]
