"""
Base classes for MCP agents.

This module defines the base classes and interfaces for all MCP agents.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TypeVar, Generic, Type
from pydantic import BaseModel, Field
import logging
from datetime import datetime
import json

# Type variable for agent configuration
TConfig = TypeVar('TConfig', bound='AgentConfig')

class AgentError(Exception):
    """Base exception for agent-related errors."""
    def __init__(self, message: str, details: Optional[Dict] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

class AgentConfig(BaseModel):
    """Base configuration for all agents."""
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for agent operations"
    )
    timeout: int = Field(
        default=30,
        description="Timeout in seconds for agent operations"
    )
    temperature: float = Field(
        default=0.7,
        description="Temperature setting for LLM-based agents",
        ge=0.0,
        le=2.0
    )
    enabled: bool = Field(
        default=True,
        description="Whether the agent is enabled"
    )

class BaseAgent(ABC, Generic[TConfig]):
    """
    Abstract base class for all MCP agents.
    
    Subclasses should implement the process() method and can override
    other methods as needed.
    """
    
    def __init__(self, config: Optional[TConfig] = None):
        """Initialize the agent with optional configuration."""
        self.config = config or self.get_default_config()
        self.logger = logging.getLogger(f"agent.{self.__class__.__name__}")
        self.metrics = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'total_processing_time': 0.0
        }
    
    @classmethod
    def get_default_config(cls) -> TConfig:
        """Return a default configuration instance for this agent."""
        return AgentConfig()
    
    async def __call__(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process input data with error handling and metrics tracking."""
        start_time = datetime.utcnow()
        self.metrics['total_calls'] += 1
        
        try:
            # Validate input
            if not await self.validate_input(input_data):
                raise AgentError("Input validation failed")
            
            # Process the input
            result = await self.process(input_data)
            
            # Update metrics
            self.metrics['successful_calls'] += 1
            self.metrics['total_processing_time'] += (
                datetime.utcnow() - start_time
            ).total_seconds()
            
            return result
            
        except Exception as e:
            self.metrics['failed_calls'] += 1
            error_details = {
                'agent': self.__class__.__name__,
                'input': input_data,
                'error': str(e)
            }
            self.logger.error(
                "Agent processing failed: %s",
                json.dumps(error_details, default=str)
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
