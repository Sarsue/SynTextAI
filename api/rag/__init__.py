"""Two pieces of retrieval plumbing.

There were seven modules here: a pipeline, a factory, an interfaces file, a
search engine and a reranker, wrapped around a query processor and a chunk
selector. The pipeline existed to hold the two useful classes, the factory
existed to build the pipeline, and the interfaces declared abstract methods
with exactly one implementation each and no prospect of a second.

Nothing called the pipeline's own process() method. The search engine was
imported by nothing at all. The reranker actively made retrieval worse and was
deleted separately. What is left is what was ever used.
"""
from .chunk_selector import SmartChunkSelector
from .query_processor import DefaultQueryProcessor

__all__ = ["SmartChunkSelector", "DefaultQueryProcessor"]
