"""
Integration tests for learning materials generation.
"""
import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock, patch

# Add the parent directory to the path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.processors.processor_utils import generate_learning_materials_for_concept
from api.services.llm_service import LLMService

# Sample test data
TEST_CONCEPT = {
    'id': 1,
    'title': 'Test Concept',
    'explanation': 'This is a test concept explanation.',
}

# Mock store object
class MockStore:
    def __init__(self):
        self.file_repo = MagicMock()
        self.file_repo.get_file.return_value = MagicMock(user_id=1)
        self.add_flashcard = MagicMock(return_value=True)
        self.add_quiz_question = MagicMock(return_value=True)

@pytest.fixture
def mock_llm_service():
    with patch('api.processors.processor_utils.llm_service') as mock_service:
        # Mock the LLM service responses
        mock_service.generate_flashcards.return_value = [
            {'question': 'What is test?', 'answer': 'This is a test'}
        ]
        mock_service.generate_mcqs.return_value = [
            {
                'question': 'What is test?',
                'answer': 'A test',
                'options': ['A test', 'Not a test', 'Maybe a test'],
                'explanation': 'This is a test explanation'
            }
        ]
        mock_service.generate_true_false_questions.return_value = [
            {'statement': 'This is a true statement', 'is_true': True, 'explanation': 'Because it is'}
        ]
        yield mock_service

def test_learning_materials_generation(mock_llm_service):
    """Test that learning materials are generated and saved correctly."""
    # Setup
    store = MockStore()
    file_id = 1
    
    # Execute
    result = generate_learning_materials_for_concept(store, file_id, TEST_CONCEPT)
    
    # Verify
    assert result is True
    
    # Verify LLM service was called with correct parameters
    mock_llm_service.generate_flashcards.assert_called_once()
    mock_llm_service.generate_mcqs.assert_called_once()
    mock_llm_service.generate_true_false_questions.assert_called_once()
    
    # Verify store methods were called
    assert store.add_flashcard.called
    assert store.add_quiz_question.called

if __name__ == "__main__":
    # Run tests directly for debugging
    test_learning_materials_generation(MagicMock())
