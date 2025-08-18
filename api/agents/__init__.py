"""
MCP Agent Framework for SynTextAI.

This package provides the base classes and utilities for creating and managing
modular agents that handle different aspects of content processing.
"""

import logging
from typing import Type, Dict, Any

from .base_agent import BaseAgent, AgentConfig, AgentError
from .agent_factory import AgentFactory

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Agent registry with metadata
AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {}

# Import agents that use the @agent decorator to ensure they're registered
from . import ingestion_agent  # noqa: F401
from . import summarization_agent  # noqa: F401

def register_agent(
    name: str,
    agent_class: Type[BaseAgent],
    config_class: Type[AgentConfig],
    description: str,
    is_dspy_agent: bool = False,
    version: str = "1.0.0"
) -> None:
    """Register a single agent with the AgentFactory."""
    try:
        # Create default config instance
        default_config = {}
        if hasattr(config_class, "model_fields"):
            default_config = {
                name: field.default
                for name, field in config_class.model_fields.items()
                if field.default is not None
            }
        
        # Register the agent
        AgentFactory.register(
            name=name,
            agent_class=agent_class,
            default_config=default_config,
            is_dspy_agent=is_dspy_agent,
            description=description,
            version=version
        )
        
        # Add to registry
        AGENT_REGISTRY[name] = {
            "class": agent_class,
            "config_class": config_class,
            "description": description,
            "is_dspy_agent": is_dspy_agent,
            "version": version
        }
        
        logger.info(f"Registered agent: {name}")
        
    except Exception as e:
        logger.error(f"Failed to register agent {name}: {str(e)}", exc_info=True)
        raise

def register_agents() -> None:
    """Register all agents with the AgentFactory.
    
    Note: Agents using the @agent decorator are automatically registered
    when their module is imported. Only agents not using the decorator
    need to be registered here.
    """
    # Import agents here to avoid circular imports
    from .qa_agent import QAAgent, QAConfig
    from .study_scheduler_agent import StudySchedulerAgent, StudySchedulerConfig
    from .integration_agent import IntegrationAgent, IntegrationAgentConfig
    
    # Register agents that don't use the @agent decorator
    register_agent(
        name="qa",
        agent_class=QAAgent,
        config_class=QAConfig,
        description="Answers questions about content",
        is_dspy_agent=True
    )
    
    register_agent(
        name="study_scheduler",
        agent_class=StudySchedulerAgent,
        config_class=StudySchedulerConfig,
        description="Schedules study sessions with spaced repetition",
        is_dspy_agent=False
    )
    
    register_agent(
        name="integration",
        agent_class=IntegrationAgent,
        config_class=IntegrationAgentConfig,
        description="Manages external service integrations",
        is_dspy_agent=False
    )

# Initialize the agent factory
agent_factory = AgentFactory()

# Export commonly used classes and functions
__all__ = [
    'BaseAgent',
    'AgentConfig',
    'AgentError',
    'AgentFactory',
    'agent_factory',
    'register_agent',
    'register_agents',
    'IntegrationType'
]
