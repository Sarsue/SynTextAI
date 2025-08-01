"""
Tests for the Summarization Agent.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.agents.summarization_agent import SummarizationAgent, SummarizationConfig
from api.agents.base_agent import AgentError

class TestSummarizationAgent:
    """Tests for the SummarizationAgent class."""
    
    @pytest.fixture
    def agent(self):
        """Fixture to create a test SummarizationAgent instance."""
        return SummarizationAgent()
    
    @pytest.mark.asyncio
    async def test_validate_input_valid(self, agent):
        """Test input validation with valid input."""
        # Test with string content
        assert await agent.validate_input({"content": "Test content"}) is True
        
        # Test with list of chunks
        assert await agent.validate_input({
            "content": [{"text": "Chunk 1"}, {"text": "Chunk 2"}],
            "language": "Spanish",
            "comprehension_level": "beginner"
        }) is True
    
    @pytest.mark.asyncio
    async def test_validate_input_missing_content(self, agent):
        """Test input validation with missing content."""
        with pytest.raises(AgentError):
            await agent.validate_input({})
    
    @pytest.mark.asyncio
    async def test_validate_input_unsupported_level(self, agent):
        """Test input validation with unsupported comprehension level."""
        with pytest.raises(AgentError) as exc_info:
            await agent.validate_input({
                "content": "Test content",
                "comprehension_level": "expert"
            })
        assert "Unsupported comprehension level" in str(exc_info.value)
    
    @pytest.mark.asyncio
    @patch('api.agents.summarization_agent.generate_key_concepts_dspy')
    @patch.object(SummarizationAgent, '_generate_summary')
    async def test_process_success(self, mock_generate_summary, mock_generate_concepts, agent):
        """Test successful processing of content."""
        # Setup mocks
        mock_generate_summary.return_value = {
            "overview": "Test summary",
            "sections": [],
            "length": 12,
            "language": "English",
            "comprehension_level": "intermediate"
        }
        
        mock_generate_concepts.return_value = {
            "key_concepts": [
                {
                    "concept_title": "Test Concept",
                    "concept_explanation": "Test explanation",
                    "confidence": 0.9
                }
            ]
        }
        
        # Test processing
        result = await agent.process({
            "content": "Test content to summarize and analyze.",
            "language": "English",
            "comprehension_level": "intermediate"
        })
        
        # Verify results
        assert result["status"] == "success"
        assert "summary" in result
        assert "key_concepts" in result
        assert len(result["key_concepts"]) > 0
        assert result["summary"]["overview"] == "Test summary"
    
    @pytest.mark.asyncio
    @patch.object(SummarizationAgent, '_generate_summary')
    async def test_process_summary_error(self, mock_generate_summary, agent):
        """Test error handling during summary generation."""
        # Setup mock to raise an exception
        mock_generate_summary.side_effect = Exception("Summary generation failed")
        
        # Test processing
        result = await agent({
            "content": "Test content",
            "language": "English",
            "comprehension_level": "intermediate"
        })
        
        # Verify error handling
        assert result["status"] == "error"
        assert "Summary generation failed" in result["message"]
    
    @pytest.mark.asyncio
    @patch('api.agents.summarization_agent.generate_key_concepts_dspy')
    @patch.object(SummarizationAgent, '_generate_summary')
    async def test_process_concepts_error(self, mock_generate_summary, mock_generate_concepts, agent):
        """Test error handling during concept extraction."""
        # Setup mocks
        mock_generate_summary.return_value = {"overview": "Test summary"}
        mock_generate_concepts.side_effect = Exception("Concept extraction failed")
        
        # Test processing
        result = await agent({
            "content": "Test content",
            "language": "English",
            "comprehension_level": "intermediate"
        })
        
        # Verify error handling
        assert result["status"] == "error"
        assert "Concept extraction failed" in result["message"]
    
    @pytest.mark.asyncio
    async def test_extract_key_concepts_formatting(self, agent):
        """Test that key concepts are properly formatted."""
        # Mock the generate_key_concepts_dspy function
        with patch('api.agents.summarization_agent.generate_key_concepts_dspy') as mock_generate:
            # Setup mock return value
            mock_generate.return_value = {
                "key_concepts": [
                    {
                        "concept_title": "Test Concept",
                        "concept_explanation": "Test explanation",
                        "source_page_number": 1,
                        "source_video_timestamp_start_seconds": 120,
                        "source_video_timestamp_end_seconds": 150,
                        "confidence": 0.9
                    }
                ]
            }
            
            # Call the method
            concepts = await agent._extract_key_concepts(
                content="Test content",
                language="English",
                level="intermediate"
            )
            
            # Verify the result
            assert len(concepts) == 1
            concept = concepts[0]
            assert concept["concept_title"] == "Test Concept"
            assert concept["concept_explanation"] == "Test explanation"
            assert concept["source_page_number"] == 1
            assert concept["source_video_timestamp_start_seconds"] == 120
            assert concept["source_video_timestamp_end_seconds"] == 150
            assert concept["confidence"] == 0.9
    
    def test_config_override(self):
        """Test that config overrides are applied correctly."""
        config = SummarizationConfig(
            max_summary_length=2000,
            max_concepts=5,
            language="Spanish",
            comprehension_level="beginner",
            include_bullet_points=False,
            include_citations=False,
            temperature=0.2
        )
        agent = SummarizationAgent(config=config)
        
        assert agent.config.max_summary_length == 2000
        assert agent.config.max_concepts == 5
        assert agent.config.language == "Spanish"
        assert agent.config.comprehension_level == "beginner"
        assert agent.config.include_bullet_points is False
        assert agent.config.include_citations is False
        assert agent.config.temperature == 0.2
