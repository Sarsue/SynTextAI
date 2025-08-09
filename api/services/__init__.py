"""
Services package for SynTextAI.

This package contains all the service layer components that handle business logic,
coordinate between different parts of the application, and interact with external services.
"""

# Import all services to make them available at the package level
from .agent_service import AgentService, agent_service
from .llm_service import LLMService, llm_service
from .embedding_service import EmbeddingService, embedding_service
from .repository_service import RepositoryService, repository_service

# Export services for easy access
__all__ = [
    'AgentService',
    'agent_service',
    'LLMService',
    'llm_service',
    'EmbeddingService',
    'embedding_service',
    'RepositoryService',
    'repository_service',
]
