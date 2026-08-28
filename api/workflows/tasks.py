import logging

from api.core import query_cache
from api.core.log_safety import safe_text
import os
import asyncio
import time
from api.core.timing import emit, stage
from api.core.seats import sync_seats_to_stripe
from api.core.utils import download_from_gcs, chunk_text, delete_from_gcs, delete_workspace_objects
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
    workspace_id: int | None,
    language: str = "en",
    comprehension_level: str = "Beginner",
) -> Dict[str, Any]:
    """Processes the uploaded file: download, extract, generate embeddings, and update database."""
    logger.info(f"Starting processing for file: {filename} (ID: {file_id}, User: {user_id})")

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
                    # Or the recreated row belongs to no workspace and nobody
                    # can see the document that was just ingested into it.
                    "workspace_id": workspace_id,
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
            download_started = time.perf_counter()

            # Fetch by URL, with the service account.
            #
            # file_url carries the full object path, so the worker no longer
            # needs to know who uploaded a document in order to read it. It used
            # to take (uploader_uid, filename) and, when the uid was missing,
            # reverse it back out of the URL — a helper that existed only
            # because storage was keyed by person while everything else was
            # keyed by workspace.
            logger.info(f"Downloading file {filename} from GCS")
            file_data = await asyncio.to_thread(download_from_gcs, file_url)
            if file_data is None:
                return await handle_processing_error(
                    file_id_int, f"Failed to download file {filename} from GCS"
                )

            logger.info(f"Downloaded file {filename}, size: {len(file_data)} bytes")
            emit(
                "download",
                ms=(time.perf_counter() - download_started) * 1000,
                file_id=file_id_int,
                bytes=len(file_data),
            )

            with stage(
                "extract_embed_store",
                file_id=file_id_int,
                processor=processor.__class__.__name__,
                bytes=len(file_data),
            ):
                result = await processor.process(
                    user_id=user_id,
                    file_id=file_id,
                    filename=filename,
                    file_data=file_data,
                    file_url=file_url,
                    language=language,
                    comprehension_level=comprehension_level,
                )

            if not result.get("success", False):
                error_msg = result.get("error", "Unknown error during file processing")
                return await handle_processing_error(
                    file_id_int, f"Processing failed for file ID {file_id}: {error_msg}"
                )

            await store.file_repo.update_file_status(file_id_int, "processed")

            # This workspace's documents just changed, so answers cached from
            # the old set are wrong now rather than in five minutes. "I uploaded
            # it and it says it cannot find it" is the failure this prevents.
            await query_cache.bump_document_version(
                workspace_id if workspace_id is not None else file.get("workspace_id")
            )
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
    cache_key_parts = dict(
        workspace_id=workspace_id,
        question=message,
        formatted_history=formatted_history,
        language=language,
        comprehension_level=comprehension_level,
        file_id=file_id,
    )

    # The same question, from the same documents, within a few minutes. Costs a
    # Redis read; saves an embedding call, a retrieval and one or more model
    # calls. Returns None whenever anything is unavailable or uncertain, so the
    # normal path below is the fallback for every failure here.
    cached = await query_cache.get(**cache_key_parts)
    if cached is not None:
        with stage("query", user_id=user_id, workspace_id=workspace_id, mode="cached") as ctx:
            ctx["chunks"] = cached.get("context_chunk_count") or 0
        logger.info({"event": "run_query_pipeline.cache_hit", "workspace_id": workspace_id})
        return cached

    try:
        logger.info({"event": "run_query_pipeline.agent_start", "message": safe_text(message)})
        with stage("query", user_id=user_id, workspace_id=workspace_id, mode="pipeline") as ctx:
            result = await query_agent.run(
                user_id=user_id,
                message=message,
                language=language,
                comprehension_level=comprehension_level,
                formatted_history=formatted_history,
                workspace_id=workspace_id,
                file_id=file_id,
            )
            ctx["chunks"] = len(result.get("context_chunks") or [])
        await query_cache.put(result=result, **cache_key_parts)
        return result
    except Exception as agent_error:
        logger.warning(
            {
                "event": "run_query_pipeline.agent_failed_fallback",
                "message": message,
                "error": str(agent_error),
            }
        )
        with stage("query", user_id=user_id, workspace_id=workspace_id, mode="fallback") as ctx:
            query_embedding = await get_text_embedding(message)
            # Same workspace-first scoping as the agent path, so the fallback
            # does not silently return nothing for invited staff.
            accessible_ids = None
            if workspace_id is None:
                accessible_ids = await store.workspace_repo.accessible_workspace_ids(user_id)
            topK_chunks = await store.file_repo.hybrid_search(
                user_id=user_id,
                query=message,
                query_embedding=query_embedding,
                workspace_id=workspace_id,
                file_id=file_id,
                top_k=10,
                accessible_workspace_ids=accessible_ids,
            )
            ctx["chunks"] = len(topK_chunks or [])
            response = await syntext.query_pipeline(message, formatted_history, topK_chunks, language, comprehension_level)
        return {
            "response": response,
            "context_chunks": topK_chunks,
            "rewritten_query": message,
            "expanded_terms": [],
            "mode": "fallback",
            "error": str(agent_error),
        }

async def delete_user_task(user_id, user_gc_id: str = None):
    """Deletes a user's account, subscription, and associated files.

    user_id arrives as an int from the route and as a str from anything that
    read it out of a payload. The subscription lookup compares against an
    integer column, so a str silently matched nothing and returned None,
    skipping the whole Stripe branch: the account vanished while the
    subscription kept billing. Normalised once, here.
    """
    try:
        user_id = int(user_id)
        # get_subscription returns (subscription, card_details), not a dict.
        # This used to call user_sub.get("status") straight on the tuple, which
        # raises AttributeError on the very first line of the task. The outer
        # except swallowed it, so deleting an account cancelled nothing, removed
        # no documents, and left the user row in place: the customer kept being
        # billed for an account they had been told was deleted.
        subscription_data = await store.user_repo.get_subscription(user_id)
        subscription = subscription_data[0] if subscription_data else None

        if subscription:
            # Cancel whatever the status is. Gating on 'active' left a past_due
            # or unpaid subscription running, which is the one case where the
            # customer is most likely to be leaving because of billing.
            stripe_sub_id = subscription.get("stripe_subscription_id")
            stripe_customer_id = subscription.get("stripe_customer_id")
            if stripe_sub_id:
                try:
                    await asyncio.to_thread(stripe.Subscription.cancel, stripe_sub_id)
                    logger.info(f"Subscription {stripe_sub_id} canceled.")
                except Exception as e:
                    # Already cancelled, or gone. Not a reason to abandon the
                    # rest of the deletion.
                    logger.warning(f"Could not cancel subscription {stripe_sub_id}: {e}")
            if stripe_customer_id:
                try:
                    payment_methods = await stripe.PaymentMethod.list_async(customer=stripe_customer_id, type="card")
                    for method in payment_methods.auto_paging_iter():
                        await stripe.PaymentMethod.detach_async(method.id)
                        logger.info(f"Payment method {method.id} detached.")
                    await stripe.Customer.delete_async(stripe_customer_id)
                    logger.info(f"Stripe customer {stripe_customer_id} deleted.")
                except Exception as e:
                    logger.warning(f"Could not delete Stripe customer {stripe_customer_id}: {e}")

        # Which organizations disappear with this person: every one they own.
        # Everything in those goes; everything in a company they were merely a
        # member of stays, whoever uploaded it.
        #
        # Owned organizations go even when other people are still in them, which
        # was not true before. The owner holds the card and the Stripe customer
        # and this task cancels both, so an organization left behind has no
        # payer, and an unsubscribed organization cannot be used at all. Keeping
        # it would leave those members a company they can neither use nor pay
        # for, with no way to become its owner: set_member_role refuses to grant
        # "owner" on purpose.
        #
        # Handing ownership to somebody staying was the other option and is
        # worse. It gives a person a company nobody can pay for, silently, and
        # billing access they never asked for.
        #
        # This destroys other people's documents, which is why
        # GET /users/deletion-impact exists and why the dialog names the numbers
        # before anybody presses anything.
        departing_orgs = set()
        surviving_orgs = []
        try:
            for m in await store.org_repo.get_memberships(user_id):
                org_id = m["organization_id"]
                others = [
                    x for x in await store.org_repo.list_members(org_id)
                    if x["user_id"] != user_id
                ]
                if m["role"] == "owner":
                    departing_orgs.add(org_id)
                elif others:
                    surviving_orgs.append(org_id)
        except Exception as org_error:
            logger.error(f"Could not resolve organizations for user {user_id}: {org_error}", exc_info=True)

        # Storage follows the workspace, so there is nothing to work out here.
        #
        # This used to resolve which organizations were departing, which
        # workspaces those held, and which of this person's uploads happened to
        # live in them — sixty-odd lines to answer a question that only existed
        # because objects were filed under the uploader. Deleting an
        # organization deletes its workspaces, and each workspace takes its own
        # prefix with it.
        for org_id in departing_orgs:
            try:
                for ws_id in await store.workspace_repo.accessible_workspace_ids(
                    user_id, organization_id=org_id
                ):
                    removed = await asyncio.to_thread(delete_workspace_objects, ws_id)
                    logger.info(f"Removed {removed} object(s) for workspace {ws_id}")
            except Exception as e:
                logger.error(f"Could not clear storage for organization {org_id}: {e}", exc_info=True)

        # Organizations this user solely owns would otherwise survive as
        # zombies: organization_members cascades away with the user, but the
        # organization row itself does not, leaving a tenant with no owner and,
        # if the id is still cached client-side, one that a later signup can
        # resolve back to.
        try:
            for org_id in departing_orgs:
                await store.org_repo.delete_organization(org_id)
                logger.info(f"Deleted organization {org_id}, its last owner is gone")
            for org_id in surviving_orgs:
                logger.info(f"Organization {org_id} kept: other members remain")
        except Exception as org_error:
            logger.error(f"Error cleaning up organizations for user {user_id}: {org_error}", exc_info=True)

        success = await store.user_repo.delete_user_account(user_id)

        # Shrink the seat count of every organization they were a member of.
        #
        # Must run after the user row is gone, since the quantity is derived
        # from a live count of members. Removing a member through the UI already
        # did this; deleting the account did not, so the organization kept
        # paying for somebody who no longer existed.
        for org_id in surviving_orgs:
            await sync_seats_to_stripe(store, org_id, reason="member account deleted")
        if success:
            logger.info(f"User account {user_id} deleted successfully")
        else:
            logger.error(f"Failed to delete user account {user_id}")
    except Exception as e:
        logger.error(f"Error during user deletion: {e}")
        raise HTTPException(status_code=500, detail="User deletion failed")

