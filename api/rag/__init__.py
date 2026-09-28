"""Retrieval plumbing: the chunk selector, which fits passages to a budget.

The query processor that lived here (rewriting a follow-up into a standalone
question, generating related search terms) went when retrieval became agentic
on 2026-09-28: the coordinator reads the conversation and writes its own
queries, as many as it needs.
"""
from .chunk_selector import SmartChunkSelector

__all__ = ["SmartChunkSelector"]
