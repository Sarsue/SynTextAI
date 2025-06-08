"""
Utility functions and shared logic for file processors.
Centralizes common functionality used by multiple processors.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

async def generate_learning_materials_for_concept(
    store, 
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
        
        concept_id = concept.get('id')
        if not concept_id:
            logger.warning(f"Cannot generate learning materials - concept has no ID: {concept.get('concept_title', 'Unknown')}")
            return False
            
        concept_title = concept.get('concept_title', '')
        concept_explanation = concept.get('concept_explanation', '')
        logger.info(f"Generating learning materials for concept ID {concept_id}: '{concept_title[:30]}...'")
        logger.debug(f"Full concept data for learning material generation: {concept}")
        
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
                        front = card.get('question', card.get('front', ''))
                        back = card.get('answer', card.get('back', ''))
                        logger.debug(f"Saving flashcard {i+1}/{len(flashcards)}: front='{front[:30]}...', back='{back[:30]}...'")
                        
                        await store.add_flashcard_async(
                            file_id=int(file_id),
                            question=front,
                            answer=back,
                            key_concept_id=int(concept_id)
                        )
                        flashcards_saved += 1
                    except Exception as e:
                        logger.error(f"Error saving flashcard {i+1}: {e}", exc_info=True)
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
                        
                        await store.add_quiz_question_async(
                            file_id=int(file_id),
                            question=question,
                            question_type="MCQ",
                            correct_answer=answer,
                            distractors=options,
                            key_concept_id=int(concept_id)
                        )
                        mcqs_saved += 1
                    except Exception as e:
                        logger.error(f"Error saving MCQ {i+1}: {e}", exc_info=True)
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
                        
                        await store.add_quiz_question_async(
                            file_id=int(file_id),
                            question=statement,
                            question_type="TRUE_FALSE",
                            correct_answer="True" if is_true else "False",
                            explanation=explanation,
                            key_concept_id=int(concept_id)
                        )
                        tf_saved += 1
                    except Exception as e:
                        logger.error(f"Error saving T/F question {i+1}: {e}", exc_info=True)
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
