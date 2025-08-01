""
Tests for the Ingestion Agent.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.agents.ingestion_agent import IngestionAgent, IngestionConfig
from api.agents.base_agent import AgentError

class TestIngestionAgent:
    """Tests for the IngestionAgent class."""
    
    @pytest.fixture
    def agent(self):
        """Fixture to create a test IngestionAgent instance."""
        return IngestionAgent()
    
    @pytest.mark.asyncio
    async def test_validate_input_valid(self, agent):
        """Test input validation with valid input."""
        # Test with content
        assert await agent.validate_input({"source_type": "text", "content": "Test content"}) is True
        
        # Test with URL
        assert await agent.validate_input({"source_type": "url", "url": "https://example.com"}) is True
    
    @pytest.mark.asyncio
    async def test_validate_input_missing_fields(self, agent):
        """Test input validation with missing required fields."""
        # Missing source_type
        with pytest.raises(AgentError):
            await agent.validate_input({"content": "Test content"})
        
        # Missing both content and url
        with pytest.raises(AgentError):
            await agent.validate_input({"source_type": "text"})
    
    @pytest.mark.asyncio
    @patch('api.agents.ingestion_agent.process_text')
    async def test_process_text(self, mock_process_text, agent):
        """Test processing text content."""
        # Mock the processor
        mock_process_text.return_value = {"chunks": ["chunk1", "chunk2"], "metadata": {}}
        
        # Test processing
        result = await agent.process({
            "source_type": "text",
            "content": "Test content"
        })
        
        # Verify results
        assert result["status"] == "success"
        assert result["source_type"] == "text"
        assert "chunks" in result["content"]
        mock_process_text.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('api.agents.ingestion_agent.process_pdf')
    async def test_process_pdf(self, mock_process_pdf, agent):
        """Test processing PDF content."""
        # Mock the processor
        mock_process_pdf.return_value = {"chunks": ["pdf_chunk1", "pdf_chunk2"], "metadata": {}}
        
        # Test processing
        result = await agent.process({
            "source_type": "pdf",
            "content": b"%PDF-1.5\n..."
        })
        
        # Verify results
        assert result["status"] == "success"
        assert result["source_type"] == "pdf"
        assert "chunks" in result["content"]
        mock_process_pdf.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('api.agents.ingestion_agent.process_youtube')
    async def test_process_youtube(self, mock_process_youtube, agent):
        """Test processing YouTube URL."""
        # Mock the processor
        mock_process_youtube.return_value = {"chunks": ["video_chunk1"], "metadata": {}}
        
        # Test processing
        result = await agent.process({
            "source_type": "youtube",
            "url": "https://youtube.com/watch?v=test123"
        })
        
        # Verify results
        assert result["status"] == "success"
        assert result["source_type"] == "youtube"
        assert "chunks" in result["content"]
        mock_process_youtube.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('api.agents.ingestion_agent.process_url')
    async def test_process_url(self, mock_process_url, agent):
        """Test processing web URL."""
        # Mock the processor
        mock_process_url.return_value = {"chunks": ["web_chunk1", "web_chunk2"], "metadata": {}}
        
        # Test processing
        result = await agent.process({
            "source_type": "url",
            "url": "https://example.com/article"
        })
        
        # Verify results
        assert result["status"] == "success"
        assert result["source_type"] == "url"
        assert "chunks" in result["content"]
        mock_process_url.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_unsupported_source_type(self, agent):
        """Test processing with an unsupported source type."""
        with pytest.raises(AgentError) as exc_info:
            await agent.process({
                "source_type": "unsupported_type",
                "content": "Test"
            })
        assert "Unsupported source type" in str(exc_info.value)
    
    @pytest.mark.asyncio
    @patch('api.agents.ingestion_agent.process_text')
    async def test_error_handling(self, mock_process_text, agent):
        """Test error handling during processing."""
        # Mock processor to raise an exception
        mock_process_text.side_effect = Exception("Test error")
        
        # Test processing
        result = await agent({
            "source_type": "text",
            "content": "Test content"
        })
        
        # Verify error handling
        assert result["status"] == "error"
        assert "Test error" in result["message"]
    
    def test_config_override(self):
        """Test that config overrides are applied correctly."""
        config = IngestionConfig(
            max_chunk_size=2000,
            chunk_overlap=100,
            supported_types=["pdf", "text"]
        )
        agent = IngestionAgent(config=config)
        
        assert agent.config.max_chunk_size == 2000
        assert agent.config.chunk_overlap == 100
        assert "pdf" in agent.config.supported_types
        assert "text" in agent.config.supported_types
        assert len(agent.config.supported_types) == 2  # Only the overridden types
