"""
Base classes and interfaces for MCP (Modular Content Processing) agents.

This module provides the foundational components for creating specialized agents that handle
different aspects of content processing in the SynTextAI system. All agents should inherit
from the BaseAgent class and implement the required interfaces.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TypeVar, Generic, Type
from pydantic import BaseModel, Field
import logging

# Type variable for configuration
TConfig = TypeVar('TConfig', bound='AgentConfig')
import logging
from datetime import datetime
import json

# Type variable for agent configuration
TConfig = TypeVar('TConfig', bound='AgentConfig')

class AgentError(Exception):
    """
    Base exception for agent-related errors.
    
    This exception should be raised when an agent encounters an error during processing.
    It includes both a user-friendly message and optional details for debugging.
    
    Args:
        message: A human-readable error message
        details: Optional dictionary with additional error details
        
    Attributes:
        message: The error message
        details: Dictionary containing additional error context
    """
    def __init__(self, message: str, details: Optional[Dict] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

class AgentConfig(BaseModel):
    """
    Base configuration model for all agents.
    
    This Pydantic model defines common configuration parameters that are available
    to all agents. Individual agents can extend this class to add agent-specific
    configuration options.
    
    Attributes:
        max_retries: Maximum number of retry attempts for agent operations
        timeout: Timeout in seconds for agent operations
        temperature: Temperature setting for LLM-based agents (0.0 to 2.0)
        enabled: Whether the agent is enabled and should process requests
    """
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for agent operations",
        ge=0
    )
    timeout: int = Field(
        default=30,
        description="Timeout in seconds for agent operations",
        gt=0
    )
    temperature: float = Field(
        default=0.7,
        description=(
            "Controls randomness in LLM outputs. Lower values make outputs "
            "more deterministic (0.0) while higher values increase randomness (2.0)."
        ),
        ge=0.0,
        le=2.0
    )
    enabled: bool = Field(
        default=True,
        description="If False, the agent will reject all processing requests"
    )

class BaseAgent(ABC, Generic[TConfig]):
    """
    Abstract base class for all MCP agents.
    
    This class provides the core functionality and interface that all agents must implement.
    It handles common tasks like input validation, error handling, and metrics collection.
    
    Subclasses must implement the `process()` method and can optionally override
    other methods to customize behavior.
    
    Example:
        ```python
        class MyAgent(BaseAgent):
            class Config(AgentConfig):
                my_param: str = "default"
                
            async def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
                # Implementation here
                return {"result": "success"}
        ```
    
    Attributes:
        config: The agent's configuration
        logger: Pre-configured logger instance for the agent
        metrics: Dictionary tracking operational metrics
    """
    
    def __init__(self, config: Optional[TConfig] = None):
        """
        Initialize the agent with optional configuration.
        
        Args:
            config: Optional configuration instance. If not provided,
                   the agent will use the default configuration.
        """
        self.config = config or self.get_default_config()
        self.logger = logging.getLogger(f"agent.{self.__class__.__name__}")
        self.metrics = {
            'total_calls': 0,            # Total number of calls made to the agent
            'successful_calls': 0,        # Number of successful calls
            'failed_calls': 0,            # Number of failed calls
            'total_processing_time': 0.0  # Total processing time in seconds
        }
    
    @classmethod
    def get_default_config(cls) -> TConfig:
        """
        Return a default configuration instance for this agent.
        
        This method creates a new instance of the agent's configuration class
        with default values. Subclasses should override this if they have a
        custom configuration class.
        
        Returns:
            A new configuration instance with default values
            
        Example:
            ```python
            @classmethod
            def get_default_config(cls) -> MyConfig:
                return MyConfig(my_param="custom_default")
            ```
        """
        return AgentConfig()
    
    async def __call__(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process input data with error handling and metrics tracking.
        
        This is the main entry point for agent processing. It wraps the `process()`
        method with common functionality like input validation, error handling,
        and metrics collection.
        
        Args:
            input_data: Dictionary containing the input data to process
            
        Returns:
            Dictionary containing the processing results or error information
            
        Raises:
            AgentError: If input validation fails or processing encounters an error
            
        Example:
            ```python
            agent = MyAgent()
            result = await agent({"text": "Sample input"})
            ```
        """
        start_time = datetime.utcnow()
        self.metrics['total_calls'] += 1
        
        try:
            # Validate input
            if not await self.validate_input(input_data):
                raise AgentError(
                    "Input validation failed",
                    details={"input": input_data}
                )
            
            # Process the input
            result = await self.process(input_data)
            
            # Update metrics
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            self.metrics['successful_calls'] += 1
            self.metrics['total_processing_time'] += processing_time
            
            self.logger.debug(
                "Successfully processed input in %.2fs",
                processing_time
            )
            
            return result
            
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            self.metrics['failed_calls'] += 1
            self.metrics['total_processing_time'] += processing_time
            
            error_details = {
                'agent': self.__class__.__name__,
                'input': input_data,
                'error': str(e),
                'processing_time': f"{processing_time:.2f}s"
            }
            
            self.logger.error(
                "Agent processing failed after %.2fs: %s",
                processing_time,
                json.dumps(error_details, default=str, indent=2)
            )
            
            return await self.handle_error(e, input_data)
    
    @abstractmethod
    async def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the input data and return the result.
        
        Args:
            input_data: Dictionary containing input data for the agent
            
        Returns:
            Dictionary containing the processing result
        """
        pass
    
    async def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """
        Validate the input data before processing.
        
        Args:
            input_data: Input data to validate
            
        Returns:
            bool: True if input is valid, False otherwise
        """
        return True
    
    async def handle_error(self, error: Exception, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle errors that occur during processing.
        
        Args:
            error: The exception that was raised
            context: Contextual information about the error
            
        Returns:
            Dictionary containing error information
        """
        return {
            'status': 'error',
            'error_type': error.__class__.__name__,
            'message': str(error),
            'agent': self.__class__.__name__
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Return current metrics for this agent instance."""
        return self.metrics.copy()
