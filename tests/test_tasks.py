import pytest
from unittest.mock import patch, MagicMock
from api.tasks import (
    generate_flashcards_from_key_concepts,
    generate_mcq_from_key_concepts,
    generate_true_false_from_key_concepts,
    _generate_smart_distractors,
    _generate_llm_distractors
)

# Sample test data
SAMPLE_KEY_CONCEPTS = [
    {
        'concept_title': 'Artificial Intelligence',
        'concept_explanation': 'The simulation of human intelligence processes by machines, especially computer systems.'
    },
    {
        'concept_title': 'Machine Learning',
        'concept_explanation': 'A subset of AI that involves training algorithms to recognize patterns in data.'
    }
]

@pytest.mark.asyncio
async def test_generate_flashcards_from_key_concepts():
    """Test flashcard generation."""
    flashcards = await generate_flashcards_from_key_concepts(SAMPLE_KEY_CONCEPTS)

    assert len(flashcards) > 0
    assert 'front' in flashcards[0]
    assert 'back' in flashcards[0]
    assert flashcards[0]['front'] == 'Artificial Intelligence'

@pytest.mark.asyncio
async def test_generate_mcq_from_key_concepts():
    """Test MCQ generation with mocked distractors."""
    with patch('api.tasks._generate_smart_distractors') as mock_smart:
        mock_smart.return_value = ['Distractor 1', 'Distractor 2', 'Distractor 3']

        mcqs = await generate_mcq_from_key_concepts(SAMPLE_KEY_CONCEPTS)

        assert len(mcqs) > 0
        assert 'question' in mcqs[0]
        assert 'options' in mcqs[0]
        assert 'answer' in mcqs[0]
        assert len(mcqs[0]['options']) == 4  # Correct + 3 distractors

@pytest.mark.asyncio
async def test_generate_true_false_from_key_concepts():
    """Test true/false generation."""
    tf_questions = await generate_true_false_from_key_concepts(SAMPLE_KEY_CONCEPTS)

    assert len(tf_questions) > 0
    assert 'statement' in tf_questions[0]
    assert 'is_true' in tf_questions[0]

@pytest.mark.asyncio
async def test_generate_smart_distractors_embeddings():
    """Test smart distractors with embeddings."""
    with patch('api.tasks.get_text_embedding') as mock_embedding:
        with patch('api.tasks.get_text_embeddings_in_batches') as mock_batch:
            mock_embedding.return_value = [[0.1, 0.2, 0.3]]
            mock_batch.return_value = [[0.15, 0.25, 0.35], [0.05, 0.1, 0.2]]

            distractors = await _generate_smart_distractors(
                SAMPLE_KEY_CONCEPTS[0],
                SAMPLE_KEY_CONCEPTS,
                'Simulation of human intelligence.'
            )

            assert isinstance(distractors, list)
            assert len(distractors) <= 3

@pytest.mark.asyncio
async def test_generate_llm_distractors():
    """Test LLM distractor generation."""
    with patch('api.tasks.generate_explanation_dspy') as mock_llm:
        mock_llm.return_value = 'Plausible wrong explanation 1. Plausible wrong explanation 2.'

        distractors = await _generate_llm_distractors(
            SAMPLE_KEY_CONCEPTS[0],
            'Simulation of human intelligence.'
        )

        assert len(distractors) > 0
        assert 'Plausible wrong explanation 1' in distractors

@pytest.mark.asyncio
async def test_empty_key_concepts():
    """Test with empty key concepts."""
    flashcards = await generate_flashcards_from_key_concepts([])
    mcqs = await generate_mcq_from_key_concepts([])
    tf = await generate_true_false_from_key_concepts([])

    assert flashcards == []
    assert mcqs == []
    assert tf == []
