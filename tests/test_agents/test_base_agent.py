""
Tests for the base agent functionality.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.agents.base_agent import BaseAgent, AgentConfig, AgentError

class TestAgentConfig:
    """Tests for the AgentConfig class."""
    
    def test_default_values(self):
        """Test that default values are set correctly."""
        config = AgentConfig()
        assert config.max_retries == 3
        assert config.timeout == 30
        assert config.temperature == 0.7
        assert config.enabled is True
    
    def test_custom_values(self):
        """Test that custom values are set correctly."""
        config = AgentConfig(
            max_retries=5,
            timeout=60,
            temperature=0.5,
            enabled=False
        )
        assert config.max_retries == 5
        assert config.timeout == 60
        assert config.temperature == 0.5
        assert config.enabled is False

class TestBaseAgent:
    """Tests for the BaseAgent class."""
    
    class TestAgent(BaseAgent):
        """Test implementation of BaseAgent."""
        async def process(self, input_data):
            return {"processed": True, "input": input_data}
    
    @pytest.mark.asyncio
    async def test_call_success(self):
        """Test successful agent call."""
        agent = self.TestAgent()
        result = await agent({"test": "data"})
        assert result["processed"] is True
        assert result["input"]["test"] == "data"
    
    @pytest.mark.asyncio
    async def test_call_validation_error(self):
        """Test agent call with validation error."""
        agent = self.TestAgent()
        agent.validate_input = AsyncMock(return_value=False)
        
        with pytest.raises(AgentError):
            await agent({"test": "data"})
    
    @pytest.mark.asyncio
    async def test_call_processing_error(self):
        """Test agent call with processing error."""
        agent = self.TestAgent()
        agent.process = AsyncMock(side_effect=Exception("Test error"))
        
        result = await agent({"test": "data"})
        assert result["status"] == "error"
        assert "Test error" in result["message"]
    
    @pytest.mark.asyncio
    async def test_metrics_tracking(self):
        """Test that metrics are tracked correctly."""
        agent = self.TestAgent()
        
        # First successful call
        await agent({"test": "data"})
        metrics = agent.get_metrics()
        assert metrics["total_calls"] == 1
        assert metrics["successful_calls"] == 1
        assert metrics["failed_calls"] == 0
        assert metrics["total_processing_time"] > 0
        
        # Second call with error
        agent.process = AsyncMock(side_effect=Exception("Test error"))
        await agent({"test": "data"})
        
        metrics = agent.get_metrics()
        assert metrics["total_calls"] == 2
        assert metrics["successful_calls"] == 1
        assert metrics["failed_calls"] == 1
