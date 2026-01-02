"""
Utility functions and shared logic for file processors.
Centralizes common functionality used by multiple processors.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple
from api.repositories.repository_manager import RepositoryManager
logger = logging.getLogger(__name__)


async def generate_learning_materials_for_concepts(
    store: RepositoryManager,
    file_id: int,
    concepts: List[Dict[str, Any]],
    comprehension_level: str = "Beginner",
    mcq_batch_size: int = 5,
) -> Dict[str, int]:
    """Generate and save learning materials for many concepts with batching.

    Flashcards + T/F are generated heuristically (no LLM). MCQs are generated in batches
    via the LLM to avoid per-concept calls.
    """
    from api.schemas.learning_content import FlashcardCreate
    from api.tasks import generate_mcqs_for_concepts_batch

    flashcards_saved = 0
    mcqs_saved = 0
    tf_saved = 0

    # Flashcards + T/F locally
    for c in concepts:
        cid = c.get('id')
        title = c.get('concept_title') or c.get('concept') or ''
        explanation = c.get('concept_explanation') or c.get('explanation') or ''
        if not cid or not title or not explanation:
            continue

        # Flashcards: 1-2 per concept
        try:
            await store.learning_material_repo.add_flashcard(
                file_id=int(file_id),
                flashcard_data=FlashcardCreate(
                    question=f"What is {title}?",
                    answer=explanation,
                    key_concept_id=int(cid),
                    is_custom=True,
                ),
            )
            flashcards_saved += 1
            first_sentence = explanation.split('.', 1)[0].strip()
            if first_sentence and len(explanation.split()) > 20:
                await store.learning_material_repo.add_flashcard(
                    file_id=int(file_id),
                    flashcard_data=FlashcardCreate(
                        question=f"What is a key detail about {title}?",
                        answer=first_sentence,
                        key_concept_id=int(cid),
                        is_custom=True,
                    ),
                )
                flashcards_saved += 1
        except Exception as e:
            logger.error(f"Error saving flashcards for concept {cid}: {e}", exc_info=True)

        # True/False: 2 per concept
        try:
            true_stmt = f"{title} refers to {explanation.split('.', 1)[0].strip()}."
            false_stmt = f"{title} is completely unrelated to the topic covered in this material."
            await store.learning_material_repo.add_quiz_question(
                file_id=int(file_id),
                question=true_stmt,
                question_type="TF",
                correct_answer="True",
                key_concept_id=int(cid),
            )
            tf_saved += 1
            await store.learning_material_repo.add_quiz_question(
                file_id=int(file_id),
                question=false_stmt,
                question_type="TF",
                correct_answer="False",
                key_concept_id=int(cid),
            )
            tf_saved += 1
        except Exception as e:
            logger.error(f"Error saving true/false for concept {cid}: {e}", exc_info=True)

    # MCQs batched via LLM
    try:
        mcqs = await generate_mcqs_for_concepts_batch(
            concepts=concepts,
            comprehension_level=comprehension_level,
            batch_size=mcq_batch_size,
        )
        for mcq in mcqs:
            try:
                cid = int(mcq.get('key_concept_id'))
                question = mcq.get('question', '')
                options = mcq.get('options', []) or []
                answer = mcq.get('answer', '')
                if not question or not options or not answer:
                    continue
                await store.learning_material_repo.add_quiz_question(
                    file_id=int(file_id),
                    question=question,
                    question_type="MCQ",
                    correct_answer=answer,
                    distractors=options,
                    key_concept_id=cid,
                )
                mcqs_saved += 1
            except Exception as e:
                logger.error(f"Error saving MCQ: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error generating batched MCQs: {e}", exc_info=True)

    logger.info(
        f"Batch learning materials generated for file {file_id}: {flashcards_saved} flashcards, {mcqs_saved} MCQs, {tf_saved} T/F"
    )
    return {
        "flashcards_saved": flashcards_saved,
        "mcqs_saved": mcqs_saved,
        "tf_saved": tf_saved,
    }

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
        
        logger.info(f"Generating learning materials for concept ID {concept_id}: '{concept_title[:30]}...'")
        logger.debug(f"Concept fields: {list(concept.keys())}, explanation length: {len(concept_explanation)}")
        
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
            flashcards = await generate_flashcards_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(flashcards)} flashcards")
            
            if flashcards:
                logger.debug(f"Saving {len(flashcards)} flashcards for concept ID {concept_id}")
                for i, card in enumerate(flashcards):
                    try:
                        # Extract front and back from the card data
                        front = card.get('front', card.get('question', ''))
                        back = card.get('back', card.get('answer', ''))
                        
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
                    except Exception as e:
                        logger.error(f"❌ Error saving flashcard {i+1}: {e}", exc_info=True)
                        logger.error(f"❌ Flashcard data: front='{front[:50]}...', back='{back[:50]}...'")
                        logger.error(f"❌ Parameters: file_id={file_id}, concept_id={concept_id}")
        except Exception as e:
            logger.error(f"Error generating flashcards: {e}", exc_info=True)
                
        # 2. Generate MCQ questions
        try:
            mcqs = await generate_mcqs_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(mcqs)} MCQs")
            
            if mcqs:
                logger.debug(f"Saving {len(mcqs)} MCQs for concept ID {concept_id}")
                for i, mcq in enumerate(mcqs):
                    try:
                        # Extract options and answer
                        question = mcq.get('question', '')
                        options = mcq.get('options', [])
                        answer = mcq.get('answer', '')
                        
                        await store.learning_material_repo.add_quiz_question(
                            file_id=int(file_id),
                            question=question,
                            question_type="MCQ",  # Keep MCQ for multiple choice questions
                            correct_answer=answer,
                            distractors=options,
                            key_concept_id=int(concept_id)
                        )
                        mcqs_saved += 1
                    except Exception as e:
                        logger.error(f"❌ Error saving MCQ {i+1}: {e}", exc_info=True)
                        logger.error(f"❌ MCQ data: question='{question[:50]}...', answer='{answer[:30]}...'")
                        logger.error(f"❌ Parameters: file_id={file_id}, concept_id={concept_id}, type=MCQ")
        except Exception as e:
            logger.error(f"Error generating MCQs: {e}", exc_info=True)
                
        # 3. Generate True/False questions
        try:
            tf_questions = await generate_true_false_from_concept(concept_title, concept_explanation)
            logger.debug(f"Generated {len(tf_questions)} T/F questions")
            
            if tf_questions:
                logger.debug(f"Saving {len(tf_questions)} True/False questions for concept ID {concept_id}")
                for i, tf in enumerate(tf_questions):
                    try:
                        statement = tf.get('statement', '')
                        is_true = tf.get('is_true', True)
                        explanation = tf.get('explanation', '')
                        
                        # Note: We're not passing explanation as it's not accepted by the method
                        await store.learning_material_repo.add_quiz_question(
                            file_id=int(file_id),
                            question=statement,
                            question_type="TF",  # Try this format instead of 'TF'
                            correct_answer="True" if is_true else "False",
                            key_concept_id=int(concept_id)
                        )
                        tf_saved += 1
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
