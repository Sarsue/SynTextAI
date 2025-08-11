"""
Agent Factory for creating and managing MCP agent instances.

This module provides a factory pattern for creating and retrieving agent instances
with proper configuration and dependency injection, supporting both JSON and DSPy agents.
"""

import logging
import importlib
import hashlib
import json
from typing import Dict, Type, Optional, Any, TypeVar
from pydantic import BaseModel, ValidationError, Field
from .base_agent import BaseAgent, AgentError

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseAgent)

class AgentMetadata(BaseModel):
    """Metadata for registered agents."""
    agent_class: Type[BaseAgent]
    default_config: Dict[str, Any] = Field(default_factory=dict)
    is_dspy_agent: bool = False
    description: str = ""
    version: str = "1.0.0"
    input_schema: Optional[Dict] = None
    output_schema: Optional[Dict] = None

class AgentFactory:
    """
    Factory for creating and managing agent instances with singleton support.
    
    This class maintains a registry of agent classes and provides methods
    for creating and retrieving agent instances with proper configuration.
    It supports both JSON-based and DSPy-based agents with proper type hints
    and configuration validation.
    """
    
    _registry: Dict[str, AgentMetadata] = {}
    _instances: Dict[str, BaseAgent] = {}
    _instance_configs: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def register(
        cls, 
        name: str, 
        agent_class: Type[T],
        default_config: Optional[Dict[str, Any]] = None,
        is_dspy_agent: bool = False,
        description: str = "",
        version: str = "1.0.0",
        input_schema: Optional[Dict] = None,
        output_schema: Optional[Dict] = None
    ) -> None:
        """
        Register an agent class with the factory.
        
        Args:
            name: Unique name for the agent type
            agent_class: The agent class to register (must be a subclass of BaseAgent)
            default_config: Default configuration for this agent type
            is_dspy_agent: Whether this is a DSPy-based agent
            description: Human-readable description of the agent's purpose
            version: Version string for the agent
            input_schema: JSON Schema describing expected input format
            output_schema: JSON Schema describing expected output format
            
        Raises:
            ValueError: If the agent class is not a subclass of BaseAgent
            ValueError: If an agent with this name is already registered
            ValidationError: If the provided configuration is invalid
        """
        if not issubclass(agent_class, BaseAgent):
            raise ValueError(f"Agent class must be a subclass of BaseAgent, got {agent_class.__name__}")
            
        if name in cls._registry:
            raise ValueError(f"Agent '{name}' is already registered")

        # Validate default config against agent's config class if available
        if default_config is not None and hasattr(agent_class, 'get_default_config'):
            try:
                agent_class.get_default_config().model_validate(default_config)
            except ValidationError as e:
                raise ValueError(f"Invalid default config for agent {name}: {str(e)}") from e
        
        # Create agent metadata
        metadata = AgentMetadata(
            agent_class=agent_class,
            default_config=default_config or {},
            is_dspy_agent=is_dspy_agent,
            description=description,
            version=version,
            input_schema=input_schema,
            output_schema=output_schema
        )
        
        cls._registry[name] = metadata
        logger.info(
            f"Registered agent: {name} (class: {agent_class.__name__}, "
            f"DSPy: {is_dspy_agent}, version: {version})"
        )
    
    @classmethod
    def _get_instance_key(cls, name: str, config: Optional[Dict[str, Any]] = None) -> str:
        """Generate a unique key for an agent instance based on name and config."""
        config_str = json.dumps(config or {}, sort_keys=True)
        config_hash = hashlib.md5(config_str.encode()).hexdigest()
        return f"{name}:{config_hash}"
    
    @classmethod
    async def get_agent(
        cls, 
        name: str, 
        config: Optional[Dict[str, Any]] = None,
        force_new: bool = False
    ) -> BaseAgent:
        """
        Get or create an agent instance by name with the given configuration.
        
        Args:
            name: Name of the agent to retrieve
            config: Optional configuration overrides (will be merged with defaults)
            force_new: If True, always create a new instance even if one exists
            
        Returns:
            An instance of the requested agent
            
        Raises:
            ValueError: If the agent is not found or configuration is invalid
            ValidationError: If the merged configuration is invalid
        """
        # Try to import the agent module if not registered
        if name not in cls._registry:
            try:
                # Try with and without _agent suffix
                module_names = [
                    f"api.agents.{name}",
                    f"api.agents.{name}_agent"
                ]
                
                for module_name in module_names:
                    try:
                        importlib.import_module(module_name)
                        if name in cls._registry:
                            break
                    except ImportError:
                        continue
                
                if name not in cls._registry:
                    raise ValueError(f"Agent {name} not found after importing module")
                    
            except Exception as e:
                logger.error(f"Failed to import agent {name}: {str(e)}", exc_info=True)
                raise ValueError(f"Failed to load agent {name}: {str(e)}") from e
        
        # Get agent metadata
        metadata = cls._registry.get(name)
        if not metadata:
            raise ValueError(f"Agent not found: {name}")
            
        # Merge configurations
        final_config = metadata.default_config.copy()
        if config:
            # Deep merge dictionaries
            for key, value in config.items():
                if key in final_config and isinstance(final_config[key], dict) and isinstance(value, dict):
                    final_config[key].update(value)
                else:
                    final_config[key] = value
        
        # Generate instance key based on name and config
        instance_key = cls._get_instance_key(name, final_config)
        
        # Create a new instance if forced or not already instantiated
        if force_new or instance_key not in cls._instances:
            try:
                # Validate config against agent's config class if available
                if hasattr(metadata.agent_class, 'get_default_config'):
                    config_model = metadata.agent_class.get_default_config()
                    validated_config = config_model.model_validate(final_config)
                else:
                    validated_config = final_config
                
                # Create and store the instance
                agent_instance = metadata.agent_class(config=validated_config)
                
                # Initialize the agent if it has an async initialize method
                if hasattr(agent_instance, 'initialize') and callable(getattr(agent_instance, 'initialize')):
                    await agent_instance.initialize()
                
                cls._instances[instance_key] = agent_instance
                cls._instance_configs[instance_key] = final_config
                
                logger.debug(
                    f"Created new instance of agent: {name} "
                    f"(DSPy: {metadata.is_dspy_agent}, config: {final_config})"
                )
                
            except ValidationError as e:
                logger.error(f"Invalid configuration for agent {name}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Failed to create agent {name}: {str(e)}")
                raise AgentError(f"Failed to initialize agent {name}") from e
        
        return cls._instances[instance_key]
    
    @classmethod
    def get_agent_metadata(cls, name: str) -> AgentMetadata:
        """
        Get metadata for a registered agent.
        
        Args:
            name: Name of the agent
            
        Returns:
            AgentMetadata: The agent's metadata
            
        Raises:
            ValueError: If the agent is not found
        """
        if name not in cls._registry:
            raise ValueError(f"Agent not found: {name}")
        return cls._registry[name]
    
    @classmethod
    def get_agent_config_template(cls, name: str) -> Dict[str, Any]:
        """
        Get the configuration template for an agent.
        
        Args:
            name: Name of the agent
            
        Returns:
            Dictionary containing the configuration template with defaults
            
        Raises:
            ValueError: If the agent is not found
        """
        metadata = cls.get_agent_metadata(name)
        if hasattr(metadata.agent_class, 'get_default_config'):
            return metadata.agent_class.get_default_config().model_dump()
        return metadata.default_config.copy()
    
    @classmethod
    def clear_instances(cls) -> None:
        """
        Clear all agent instances (useful for testing).
        
        This will force new agent instances to be created on next request.
        """
        cls._instances.clear()
        cls._instance_configs.clear()
        logger.debug("Cleared all agent instances")
    
    @classmethod
    def get_available_agents(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all registered agents.
        
        Returns:
            Dictionary mapping agent names to their metadata
        """
        return {
            name: {
                "class": metadata.agent_class.__name__,
                "description": metadata.description,
                "version": metadata.version,
                "is_dspy_agent": metadata.is_dspy_agent,
                "input_schema": metadata.input_schema,
                "output_schema": metadata.output_schema,
                "config_template": cls.get_agent_config_template(name)
            }
            for name, metadata in cls._registry.items()
        }


def agent(
    name: str, 
    config: Optional[Dict[str, Any]] = None,
    is_dspy_agent: bool = False,
    description: str = "",
    version: str = "1.0.0",
    input_schema: Optional[Dict] = None,
    output_schema: Optional[Dict] = None
):
    """
    Decorator for registering agent classes with metadata and configuration.
    
    Example:
        @agent(
            name="ingestion",
            config={"timeout": 60, "max_retries": 3},
            description="Handles document ingestion and preprocessing",
            version="1.0.0",
            input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}}
        )
        class IngestionAgent(BaseAgent):
            ...
    
    Args:
        name: Unique name for the agent
        config: Optional default configuration
        is_dspy_agent: Whether this is a DSPy-based agent
        description: Human-readable description of the agent's purpose
        version: Version string for the agent
        input_schema: JSON Schema describing expected input format
        output_schema: JSON Schema describing expected output format
    """
    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        AgentFactory.register(
            name=name,
            agent_class=cls,
            default_config=config or {},
            is_dspy_agent=is_dspy_agent,
            description=description,
            version=version,
            input_schema=input_schema,
            output_schema=output_schema
        )
        return cls
    return decorator
