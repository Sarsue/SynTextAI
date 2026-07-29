import logging
import os
import asyncio
import requests
from api.core.utils import format_timestamp, download_from_gcs, chunk_text, delete_from_gcs
from api.repositories.repository_manager import RepositoryManager
from api.services.llm_service import get_text_embeddings_in_batches, get_text_embedding
from api.services.syntext_agent import SyntextAgent
import stripe
from api.core.websocket_manager import websocket_manager
from dotenv import load_dotenv
from fastapi import HTTPException
import gc
from typing import Optional, List, Dict, Any
from api.models.async_db import get_database_url
from api.processors.factory import FileProcessingFactory
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

