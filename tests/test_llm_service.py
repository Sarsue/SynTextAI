import pytest
from unittest.mock import patch, MagicMock
from api.llm_service import generate_key_concepts, KeyConcept

# Sample test data
SAMPLE_TEXT = "Artificial intelligence is the simulation of human intelligence in machines."

@pytest.mark.asyncio
async def test_generate_key_concepts_success():
    """Test successful key concepts generation."""
    with patch('api.llm_service.mistral_client') as mock_client:
        # Mock the Mistral response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '[{\"concept_title\": \"AI\", \"concept_explanation\": \"Simulation of human intelligence.\"}]'
        mock_client.chat.return_value = mock_response

        concepts = generate_key_concepts(SAMPLE_TEXT)

        assert len(concepts) > 0
        assert concepts[0]['concept_title'] == 'AI'
        assert concepts[0]['concept_explanation'] == 'Simulation of human intelligence.'

@pytest.mark.asyncio
async def test_generate_key_concepts_empty_input():
    """Test with empty input."""
    with patch('api.llm_service.mistral_client') as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '[]'
        mock_client.chat.return_value = mock_response
        concepts = generate_key_concepts("")
        assert concepts == []

@pytest.mark.asyncio
async def test_key_concept_validation():
    """Test KeyConcept model validation."""
    valid_data = {
        'concept_title': 'Test Concept',
        'concept_explanation': 'This is a test explanation.',
        'source_page_number': 1
    }
    concept = KeyConcept(**valid_data)
    assert concept.concept_title == 'Test Concept'

    # Test invalid data
    invalid_data = {'concept_title': '', 'concept_explanation': ''}
    with pytest.raises(Exception):
        KeyConcept(**invalid_data)
