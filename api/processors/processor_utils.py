"""
Utility functions and shared logic for file processors.
Centralizes common functionality used by multiple processors.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple
from api.repositories.repository_manager import RepositoryManager
logger = logging.getLogger(__name__)

async def generate_learning_materials_for_concept(
    store: RepositoryManager, 
    file_id: int, 
    concept: Dict[str, Any]
) -> bool:
    """
    Generate and save learning materials for a single key concept.
    Used by multiple processors to avoid code duplication.
    
    Args:
        store: The repository store object with database access methods
        file_id: ID of the file
        concept: A single key concept with database ID
        
    Returns:
        bool: Success status
    """
    try:
        # Import needed functions directly using absolute imports
        from api.tasks import (
            generate_flashcards_from_concept,
            generate_mcqs_from_concept,
            generate_true_false_from_concept
        )
        from api.schemas.learning_content import FlashcardCreate
        
        concept_id = concept.get('id')
        if not concept_id:
            logger.warning(f"Cannot generate learning materials - concept has no ID: {concept.get('concept_title', concept.get('concept', 'Unknown'))}")
            return False
            
        # Handle both naming conventions to ensure compatibility
        concept_title = concept.get('concept_title', concept.get('concept', ''))
        concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))
        
        # Log complete field data to help with debugging
        logger.info(f"Generating learning materials for concept ID {concept_id}: '{concept_title[:30]}...'")
        logger.debug(f"Full concept fields: {list(concept.keys())}")
        logger.debug(f"Extracted concept_title: '{concept_title}', concept_explanation length: {len(concept_explanation)}")
        logger.debug(f"Full concept data for learning material generation: {concept}")
        
        # Ensure we actually have content to work with
        if not concept_title or not concept_explanation:
            logger.error(f"Missing concept title or explanation for concept ID {concept_id}. Title: '{concept_title}', Explanation length: {len(concept_explanation)}")
            return False
        
        # Track success for each material type
        flashcards_saved = 0
        mcqs_saved = 0
        tf_saved = 0
            
        # 1. Generate flashcards for this concept
        try:
            logger.debug(f"Calling generate_flashcards_from_concept for '{concept_title[:30]}...'")
            flashcards = await generate_flashcards_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(flashcards)} flashcards: {flashcards}")
            
            if flashcards:
                logger.info(f"Saving {len(flashcards)} flashcards for concept ID {concept_id}")
                for i, card in enumerate(flashcards):
                    try:
                        # Extract front and back from the card data
                        front = card.get('front', card.get('question', ''))
                        back = card.get('back', card.get('answer', ''))
                        logger.debug(f"Saving flashcard {i+1}/{len(flashcards)}: front='{front[:30]}...', back='{back[:30]}...'")
                        
                        # Create FlashcardCreate object with correct field names
                        flashcard_data = FlashcardCreate(
                            question=front,  # Use 'question' field
                            answer=back,     # Use 'answer' field
                            key_concept_id=int(concept_id),
                            is_custom=True
                        )
                        
                        # Call with correct parameters
                        await store.learning_material_repo.add_flashcard(
                            file_id=int(file_id),
                            flashcard_data=flashcard_data
                        )
                        flashcards_saved += 1
                        logger.debug(f"✅ Successfully saved flashcard {i+1}")
                    except Exception as e:
                        logger.error(f"❌ Error saving flashcard {i+1}: {e}", exc_info=True)
                        logger.error(f"❌ Flashcard data: front='{front[:50]}...', back='{back[:50]}...'")
                        logger.error(f"❌ Parameters: file_id={file_id}, concept_id={concept_id}")
        except Exception as e:
            logger.error(f"Error generating flashcards: {e}", exc_info=True)
                
        # 2. Generate MCQ questions
        try:
            logger.debug(f"Calling generate_mcqs_from_concept for '{concept_title[:30]}...'")
            mcqs = await generate_mcqs_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(mcqs)} MCQs: {mcqs}")
            
            if mcqs:
                logger.info(f"Saving {len(mcqs)} MCQs for concept ID {concept_id}")
                for i, mcq in enumerate(mcqs):
                    try:
                        # Extract options and answer
                        question = mcq.get('question', '')
                        options = mcq.get('options', [])
                        answer = mcq.get('answer', '')
                        logger.debug(f"Saving MCQ {i+1}/{len(mcqs)}: question='{question[:30]}...', answer='{answer[:30]}...'")
                        
                        await store.learning_material_repo.add_quiz_question(
                            file_id=int(file_id),
                            question=question,
                            question_type="MCQ",  # Keep MCQ for multiple choice questions
                            correct_answer=answer,
                            distractors=options,
                            key_concept_id=int(concept_id)
                        )
                        mcqs_saved += 1
                        logger.debug(f"✅ Successfully saved MCQ {i+1}")
                    except Exception as e:
                        logger.error(f"❌ Error saving MCQ {i+1}: {e}", exc_info=True)
                        logger.error(f"❌ MCQ data: question='{question[:50]}...', answer='{answer[:30]}...'")
                        logger.error(f"❌ Parameters: file_id={file_id}, concept_id={concept_id}, type=MCQ")
        except Exception as e:
            logger.error(f"Error generating MCQs: {e}", exc_info=True)
                
        # 3. Generate True/False questions
        try:
            logger.debug(f"Calling generate_true_false_from_concept for '{concept_title[:30]}...'")
            tf_questions = await generate_true_false_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(tf_questions)} T/F questions: {tf_questions}")
            
            if tf_questions:
                logger.info(f"Saving {len(tf_questions)} True/False questions for concept ID {concept_id}")
                for i, tf in enumerate(tf_questions):
                    try:
                        statement = tf.get('statement', '')
                        is_true = tf.get('is_true', True)
                        explanation = tf.get('explanation', '')
                        logger.debug(f"Saving T/F {i+1}/{len(tf_questions)}: statement='{statement[:30]}...', is_true={is_true}")
                        
                        # Note: We're not passing explanation as it's not accepted by the method
                        await store.learning_material_repo.add_quiz_question(
                            file_id=int(file_id),
                            question=statement,
                            question_type="TF",  # Try this format instead of 'TF'
                            correct_answer="True" if is_true else "False",
                            key_concept_id=int(concept_id)
                        )
                        tf_saved += 1
                        logger.debug(f"✅ Successfully saved T/F question {i+1}")
                    except Exception as e:
                        logger.error(f"❌ Error saving T/F question {i+1}: {e}", exc_info=True)
                        logger.error(f"❌ T/F data: statement='{statement[:50]}...', is_true={is_true}")
                        logger.error(f"❌ Parameters: file_id={file_id}, concept_id={concept_id}, type=TF")
        except Exception as e:
            logger.error(f"Error generating True/False questions: {e}", exc_info=True)
        
        # Log summary of materials saved
        total_items = flashcards_saved + mcqs_saved + tf_saved
        logger.info(f"Learning materials generated for concept ID {concept_id}: {flashcards_saved} flashcards, {mcqs_saved} MCQs, {tf_saved} T/F questions")
        
        return total_items > 0  # Success if at least one item was saved
        
    except Exception as e:
        logger.error(f"Error generating learning materials: {e}", exc_info=True)
        return False

async def log_concept_processing_summary(concept_results: List[bool], file_id: int) -> Dict[str, Any]:
    """
    Log a summary of concept processing results and return a summary.
    
    Args:
        concept_results: List of boolean success values from processing each concept
        file_id: The file ID
        
    Returns:
        Dict with summary of successful and failed concepts
    """
    # Summarize concept processing results
    successful_concepts = sum(1 for result in concept_results if result)
    failed_concepts = len(concept_results) - successful_concepts
    
    logger.info(f"Completed processing {len(concept_results)} concepts for file {file_id}.")
    logger.info(f"Summary: {successful_concepts} concepts processed successfully, {failed_concepts} failed")
    
    return {
        "concepts_processed": len(concept_results),
        "concepts_successful": successful_concepts,
        "concepts_failed": failed_concepts
    }
