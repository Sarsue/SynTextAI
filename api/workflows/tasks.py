import logging
import json
import re
import os
import asyncio
import requests
from api.core.utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from api.repositories.repository_manager import RepositoryManager
from api.services.llm_service import get_text_embeddings_in_batches, get_text_embedding, generate_mcq_from_key_concepts as llm_generate_mcq_from_key_concepts
from api.services.syntext_agent import SyntextAgent
import stripe
from api.core.websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import HTTPException
import gc
from typing import Optional, List, Dict, Any
from api.models.async_db import get_database_url
from api.processors.factory import FileProcessingFactory
import numpy as np  # Added for distractor generation
from urllib.parse import urlparse
from api.agents.query_agent import QueryAgent
from api.agents.ingestion_agent import IngestionAgent

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

LANGUAGE_CODE_MAP = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
}

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET')

# Initialize DocSynthStore and SyntextAgent
DATABASE_URL = get_database_url()
store = RepositoryManager(database_url=DATABASE_URL)
syntext = SyntextAgent()
query_agent = QueryAgent(store=store, syntext=syntext)

class FileUtils:
    """Utility class for file-related operations."""

    @staticmethod
    def determine_file_type(filename: str) -> str:
        """Determine file type based on filename."""
        if filename.lower().endswith(".pdf"):
            return "pdf"
        else:
            return "unknown"

async def handle_processing_error(file_id: int, error_msg: str) -> dict:
    """Handle errors during file processing by updating status and logging."""
    error_msg = error_msg[:1000]  # Truncate for consistency
    logger.error(error_msg)
    try:
        await store.file_repo.update_file_status(file_id, "failed")
    except Exception as db_error:
        logger.error(f"Failed to update file status to failed for {file_id}: {db_error}")
    return {"success": False, "error_message": error_msg}


async def _process_file_data_impl(
    *,
    user_id: int,
    file_id: int,
    filename: str,
    file_url: str,
    user_gc_id: str,
    workspace_id: int | None,
    language: str = "en",
    comprehension_level: str = "Beginner",
) -> Dict[str, Any]:
    """Processes the uploaded file: download, extract, generate embeddings, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id})")

    def _infer_user_gc_id_from_file_url(url: str) -> Optional[str]:
        """Infer firebase uid / GCS prefix from a public GCS URL.

        Expected patterns:
        - https://storage.googleapis.com/<bucket>/<user_gc_id>/<filename>
        - https://storage.googleapis.com/<bucket>/<user_gc_id>/...
        """
        try:
            if not url:
                return None
            parsed = urlparse(url)
            # Path looks like: /<bucket>/<user_gc_id>/<filename>
            parts = [p for p in (parsed.path or "").split("/") if p]
            if len(parts) < 2:
                return None
            inferred = parts[1]
            return inferred or None
        except Exception:
            return None

    async with store.file_repo.get_async_session() as transaction:  # Start transaction
        try:
            # Validate inputs
            try:
                file_id_int = int(file_id)
            except ValueError:
                return await handle_processing_error(file_id, f"Invalid file_id: {file_id}")

            if not filename or len(filename) > 255:
                return await handle_processing_error(file_id, f"Invalid filename: {filename}")

            if language.lower() not in LANGUAGE_CODE_MAP:
                logger.warning(f"Unsupported language: {language}, defaulting to English")
                language = "English"

            # Verify or create file record
            file = await store.file_repo.get_file_by_id(file_id_int)
            if not file:
                logger.info(f"File ID {file_id} not found, creating new file record")
                file_data = {
                    "user_id": int(user_id),
                    "filename": filename,
                    "file_type": FileUtils.determine_file_type(filename),
                    "status": "pending",
                    "url": file_url,
                }
                file_result = await store.file_repo.create_file(file_data)
                if not file_result or not file_result.get("id"):
                    logger.error(f"Failed to create file record for file_id: {file_id}")
                    return await handle_processing_error(
                        file_id_int, f"Failed to create file record for file_id: {file_id}"
                    )
                logger.info(f"Created file record for file_id: {file_id}")
                file = file_result

            # Update file type if not set
            if not file.get("file_type"):
                new_file_type = FileUtils.determine_file_type(filename)
                success = await store.file_repo.update_file_type(file_id_int, new_file_type)
                if not success:
                    return await handle_processing_error(
                        file_id_int, f"Failed to update file type for file ID: {file_id}"
                    )

            # Check processing status
            if file.get("processing_status") == "processed":
                logger.info(f"File {file_id} is already processed")
                return {"success": True, "final_status": "processed", "error_message": None}

            # Update status to extracting
            await store.file_repo.update_file_status(file_id_int, "extracting")

            # Initialize processor
            factory = FileProcessingFactory(store)
            processor = factory.get_processor(filename)
            if not processor:
                return await handle_processing_error(file_id_int, f"No processor available for file {filename}")

            logger.info(f"Using processor: {processor.__class__.__name__}")

            # Process file
            file_data = None

            if file_url:
                try:
                    logger.info(f"Downloading file {filename} from file_url")
                    resp = await asyncio.to_thread(requests.get, file_url, timeout=30)
                    if resp.ok and resp.content:
                        file_data = resp.content
                    else:
                        logger.warning(f"Failed to download file from file_url (status={resp.status_code})")
                except Exception as e:
                    logger.warning(f"Error downloading file from file_url: {e}")

            if file_data is None:
                logger.info(f"Downloading file {filename} from GCS")
                if not user_gc_id:
                    inferred_gc_id = _infer_user_gc_id_from_file_url(file_url)
                    if inferred_gc_id:
                        logger.info("user_gc_id missing; inferred from file_url for GCS download")
                        user_gc_id = inferred_gc_id
                file_data = download_from_gcs(user_gc_id, filename)
            if file_data is None:
                return await handle_processing_error(
                    file_id_int, f"Failed to download file {filename} from GCS"
                )

            logger.info(f"Downloaded file {filename}, size: {len(file_data)} bytes")
            result = await processor.process(
                user_id=user_id,
                file_id=file_id,
                filename=filename,
                file_data=file_data,
                file_url=file_url,
                user_gc_id=user_gc_id,
                language=language,
                comprehension_level=comprehension_level,
            )

            if not result.get("success", False):
                error_msg = result.get("error", "Unknown error during file processing")
                return await handle_processing_error(
                    file_id_int, f"Processing failed for file ID {file_id}: {error_msg}"
                )

            await store.file_repo.update_file_status(file_id_int, "processed")
            logger.info(f"File processing completed successfully for {filename}")
            return {
                "success": True,
                "final_status": "processed",
                "message": f"Successfully processed file {filename}",
                "error_message": None,
            }

        except Exception as e:
            error_msg = f"Fatal error in file processing pipeline: {str(e)[:1000]}"
            return await handle_processing_error(file_id_int, error_msg)

async def process_file_data(
    user_id: int,
    file_id: int,
    filename: str,
    file_url: str,
    user_gc_id: str,
    workspace_id: int | None,
    language: str = "en",
    comprehension_level: str = "Beginner",
) -> Dict[str, Any]:
    """Processes the uploaded file via the LangGraph ingestion agent with a safe fallback."""
    try:
        ingestion_agent = IngestionAgent(process_fn=_process_file_data_impl)
        return await ingestion_agent.run(
            user_id=user_id,
            file_id=file_id,
            filename=filename,
            file_url=file_url,
            user_gc_id=user_gc_id,
            workspace_id=workspace_id,
            language=language,
            comprehension_level=comprehension_level,
        )
    except Exception as agent_error:
        logger.warning(
            {
                "event": "process_file_data.agent_failed_fallback",
                "file_id": file_id,
                "error": str(agent_error),
            }
        )
        return await _process_file_data_impl(
            user_id=user_id,
            file_id=file_id,
            filename=filename,
            file_url=file_url,
            user_gc_id=user_gc_id,
            workspace_id=workspace_id,
            language=language,
            comprehension_level=comprehension_level,
        )


async def run_query_pipeline(
    *,
    user_id: int,
    message: str,
    language: str,
    comprehension_level: str,
    formatted_history: str = "",
    workspace_id: int | None = None,
    file_id: int | None = None,
) -> Dict[str, Any]:
    """Run retrieval + generation for a single query without persisting chat messages."""
    try:
        logger.info({"event": "run_query_pipeline.agent_start", "message": message})
        return await query_agent.run(
            user_id=user_id,
            message=message,
            language=language,
            comprehension_level=comprehension_level,
            formatted_history=formatted_history,
            workspace_id=workspace_id,
            file_id=file_id,
        )
    except Exception as agent_error:
        logger.warning(
            {
                "event": "run_query_pipeline.agent_failed_fallback",
                "message": message,
                "error": str(agent_error),
            }
        )
        query_embedding = get_text_embedding(message)
        topK_chunks = await store.file_repo.hybrid_search(
            user_id=user_id,
            query=message,
            query_embedding=query_embedding,
            workspace_id=workspace_id,
            file_id=file_id,
            top_k=10,
        )
        response = syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
        return {
            "response": response,
            "context_chunks": topK_chunks,
            "rewritten_query": message,
            "expanded_terms": [],
            "mode": "fallback",
            "error": str(agent_error),
        }

async def process_query_data(
    message: str,
    language: str,
    comprehension_level: str,
    id: int | None = None,
    history_id: int | None = None,
    workspace_id: int | None = None,
    file_id: int | None = None,
):
    """Processes a user query and generates a response using SyntextAgent with enhanced RAG."""
    if id is None or history_id is None:
        raise HTTPException(status_code=400, detail="Missing user_id or history_id for query processing")
    try:
        # Get conversation history in formatted form
        formatted_history = await store.chat_repo.format_user_chat_history(history_id, id)

        result = await run_query_pipeline(
            user_id=id,
            message=message,
            language=language,
            comprehension_level=comprehension_level,
            formatted_history=formatted_history,
            workspace_id=workspace_id,
            file_id=file_id,
        )

        response = result["response"]
        
        # Save response and notify user
        await store.chat_repo.add_message(content=response, sender='bot', user_id=id, chat_history_id=history_id)
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "success", "history_id": history_id, "message": response})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket notification for successful query processing: {str(ws_error)}")
    
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        try:
            await websocket_manager.send_message(id, "message_received", {"status": "error", "error": str(e)})
        except Exception as ws_error:
            logger.warning(f"Failed to send WebSocket error notification: {str(ws_error)}")
        raise HTTPException(status_code=500, detail="Query processing failed")

async def delete_user_task(user_id: str, user_gc_id: str):
    """Deletes a user's account, subscription, and associated files."""
    try:
        user_sub = await store.user_repo.get_subscription(user_id)
        if user_sub and user_sub.get("status") == "active":
            stripe_sub_id = user_sub.get("stripe_subscription_id")
            stripe_customer_id = user_sub.get("stripe_customer_id")
            if stripe_sub_id:
                stripe.Subscription.delete(stripe_sub_id)
                logger.info(f"Subscription {stripe_sub_id} canceled.")
            if stripe_customer_id:
                payment_methods = await stripe.PaymentMethod.list_async(customer=stripe_customer_id, type="card")
                for method in payment_methods.auto_paging_iter():
                    await stripe.PaymentMethod.detach_async(method.id)
                    logger.info(f"Payment method {method.id} detached.")
                await stripe.Customer.delete_async(stripe_customer_id)
                logger.info(f"Stripe customer {stripe_customer_id} deleted.")

        # Delete files and user account
        files = await store.file_repo.get_files_for_user(user_id)
        await asyncio.gather(
            *(asyncio.to_thread(delete_from_gcs, user_gc_id, f["name"]) for f in files),
            return_exceptions=True
        )

        success = await store.user_repo.delete_user_account(user_id)
        if success:
            logger.info(f"User account {user_id} deleted successfully")
        else:
            logger.error(f"Failed to delete user account {user_id}")
    except Exception as e:
        logger.error(f"Error during user deletion: {e}")
        raise HTTPException(status_code=500, detail="User deletion failed")

async def generate_mcq_from_key_concepts(key_concepts: List[Dict[str, Any]], comprehension_level: str = "Beginner") -> List[Dict[str, Any]]:
    """
    Generate multiple choice questions from key concepts using LLM service.

    Args:
        key_concepts: List of key concept dictionaries
        comprehension_level: Target comprehension level (e.g., Beginner, Intermediate)

    Returns:
        List of MCQ dictionaries with question, options, and answer
    """
    if not key_concepts:
        logger.warning("No key concepts provided for MCQ generation")
        return []

    try:
        # Generate MCQs using LLM service (not async, returns list directly)
        mcqs = llm_generate_mcq_from_key_concepts(key_concepts, comprehension_level)

        if len(mcqs) == 0:
            logger.debug(
                f"LLM returned 0 MCQs for {len(key_concepts)} concept(s); applying heuristic fallback"
            )

            # Heuristic fallback to guarantee at least one MCQ
            concept = key_concepts[0]
            title = concept.get('concept_title') or concept.get('concept') or 'this concept'
            explanation = (concept.get('concept_explanation') or concept.get('explanation') or '').strip()

            # Build a simple definition question
            first_sentence = explanation.split('.')
            first_sentence = first_sentence[0].strip() if first_sentence and first_sentence[0].strip() else explanation
            if not first_sentence:
                first_sentence = 'a key idea discussed in this material'

            question = f"What best describes {title}?"
            correct = first_sentence

            # Collect distractors from other concepts if available
            other_concepts = [c for c in key_concepts[1:] if c is not concept]
            distractors: List[str] = []
            for oc in other_concepts:
                oc_exp = (oc.get('concept_explanation') or oc.get('explanation') or '').strip()
                oc_sent = oc_exp.split('.')
                oc_sent = oc_sent[0].strip() if oc_sent and oc_sent[0].strip() else oc_exp
                if oc_sent and oc_sent.lower() != correct.lower():
                    distractors.append(oc_sent)
                if len(distractors) >= 3:
                    break

            # If still not enough distractors, add generic plausible ones
            generic_pool = [
                "An unrelated historical fact not covered here",
                "A peripheral detail with no direct connection",
                "A common misconception about the topic",
                "A random example that does not apply in this context",
            ]
            for g in generic_pool:
                if len(distractors) >= 3:
                    break
                if g.lower() != correct.lower():
                    distractors.append(g)

            options = [correct] + distractors[:3]
            # Stable shuffle alternative without randomness (keep as is); correct is at index 0
            fallback_mcq = {
                'question': question,
                'options': options,
                'answer': correct,
            }
            logger.info("Heuristic MCQ fallback produced 1 MCQ")
            return [fallback_mcq]

        logger.info(f"Generated {len(mcqs)} MCQs from {len(key_concepts)} key concepts")
        return mcqs
    except Exception as e:
        logger.error(f"Error generating MCQs: {e}", exc_info=True)
        return []


async def generate_flashcards_from_key_concepts(key_concepts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate flashcards from key concepts using LLM service.

    Args:
        key_concepts: List of key concept dictionaries

    Returns:
        List of flashcard dictionaries with 'front' and 'back' keys
    """
    if not key_concepts:
        logger.warning("No key concepts provided for flashcard generation")
        return []

    try:
        flashcards = []
        for concept in key_concepts:
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))

            if not concept_title or not concept_explanation:
                continue

            flashcards.append({
                'front': concept_title.strip(),
                'back': concept_explanation.strip()
            })

            if len(concept_explanation.split()) > 20:
                parts = concept_explanation.split(". ")
                if len(parts) > 1:
                    key_detail = parts[0]
                    flashcards.append({
                        'front': f"What is a key detail about {concept_title}?",
                        'back': key_detail
                    })

        logger.info(f"Generated {len(flashcards)} flashcards from {len(key_concepts)} key concepts")
        return flashcards
    except Exception as e:
        logger.error(f"Error generating flashcards: {e}", exc_info=True)
        return []


async def generate_true_false_from_key_concepts(key_concepts: list) -> list:
    """
    Generate true/false questions from key concepts.

    Args:
        key_concepts: List of key concept dictionaries

    Returns:
        List of true/false dictionaries with statement and is_true flag
    """
    if not key_concepts:
        logger.warning("No key concepts provided for true/false generation")
        return []

    try:
        tf_questions = []
        for concept in key_concepts:
            concept_title = concept.get('concept_title', concept.get('concept', ''))
            concept_explanation = concept.get('concept_explanation', concept.get('explanation', ''))

            if not concept_title or not concept_explanation:
                continue

            tf_questions.append({
                'statement': f"{concept_title} refers to {concept_explanation.split('.')[0]}.",
                'is_true': True
            })

            false_statement = f"{concept_title} is completely unrelated to the topic covered in this material."
            tf_questions.append({
                'statement': false_statement,
                'is_true': False
            })

        logger.info(f"Generated {len(tf_questions)} True/False questions from {len(key_concepts)} key concepts")
        return tf_questions
    except Exception as e:
        logger.error(f"Error generating True/False questions: {e}", exc_info=True)
        return []


async def generate_flashcards_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate flashcards for a single concept.

    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept

    Returns:
        List of flashcard items
    """
    logger.info(f"Generating flashcards for concept: '{concept_title[:30]}...'")

    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    flashcards = await generate_flashcards_from_key_concepts(concept)
    logger.info(f"Generated {len(flashcards)} flashcards for concept '{concept_title[:30]}...'")
    return flashcards


async def generate_mcqs_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate multiple choice questions for a single concept.

    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept

    Returns:
        List of MCQ items
    """
    logger.info(f"Generating MCQs for concept: '{concept_title[:30]}...'")

    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    mcqs = await generate_mcq_from_key_concepts(concept)
    logger.info(f"Generated {len(mcqs)} MCQs for concept '{concept_title[:30]}...'")
    return mcqs


async def generate_mcqs_for_concepts_batch(
    concepts: List[Dict[str, Any]],
    comprehension_level: str = "Beginner",
    batch_size: int = 5,
) -> List[Dict[str, Any]]:
    """Generate MCQs for multiple concepts in a small number of LLM calls.

    Returns a flat list of MCQs, each including key_concept_id.
    """
    if not concepts:
        return []

    from api.llm_service import gradient_chat

    all_mcqs: List[Dict[str, Any]] = []
    for i in range(0, len(concepts), batch_size):
        batch = concepts[i:i + batch_size]
        # Keep only fields we need and ensure each has an id
        items = [
            {
                "key_concept_id": int(c["id"]),
                "concept_title": c.get("concept_title") or c.get("concept") or "",
                "concept_explanation": c.get("concept_explanation") or c.get("explanation") or "",
            }
            for c in batch
            if c.get("id") is not None
        ]
        if not items:
            continue

        prompt = (
            "You are an expert educator. Create exactly 1 multiple-choice question per concept.\n"
            f"Write for {comprehension_level} level.\n\n"
            "Output ONLY JSON: an array of objects with fields:\n"
            "- key_concept_id (integer, must match input)\n"
            "- question (string)\n"
            "- options (array of 4 strings)\n"
            "- answer (string; must be one of options)\n\n"
            "Concepts JSON:\n"
            f"{json.dumps(items)}\n\n"
            "JSON array:" 
        )

        raw = gradient_chat(prompt, max_tokens=1200)
        if not raw:
            continue

        raw = re.sub(r"```(?:json)?\n?", "", raw).strip("` \n")
        try:
            parsed = json.loads(raw)
        except Exception:
            # Try extracting a JSON array substring
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else []

        if isinstance(parsed, list):
            for mcq in parsed:
                if not isinstance(mcq, dict):
                    continue
                if mcq.get("key_concept_id") is None:
                    continue
                all_mcqs.append(mcq)

    return all_mcqs


async def generate_true_false_from_concept(concept_title: str, concept_explanation: str) -> list:
    """
    Generate true/false questions for a single concept.

    Args:
        concept_title: Title of the concept
        concept_explanation: Explanation of the concept

    Returns:
        List of true/false questions
    """
    logger.info(f"Generating True/False questions for concept: '{concept_title[:30]}...'")

    concept = [{"concept_title": concept_title, "concept_explanation": concept_explanation}]
    tf_questions = await generate_true_false_from_key_concepts(concept)
    logger.info(f"Generated {len(tf_questions)} True/False questions for concept '{concept_title[:30]}...'")
    return tf_questions