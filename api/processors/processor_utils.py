import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.models import File, KeyConcept, Flashcard, QuizQuestion
from api.repositories import get_repository_manager
from api.repositories.async_file_repository import AsyncFileRepository
from api.services.llm_service import llm_service

logger = logging.getLogger(__name__)


# -------------------------------
# Dataclasses
# -------------------------------
@dataclass
class LearningMaterialResult:
    concept_id: int
    material_type: str
    success: bool
    error: Optional[str] = None
    retry_count: int = 0
    duration: float = 0.0
    created_count: int = 0


@dataclass
class LearningMaterialsSummary:
    file_id: int
    total_concepts: int
    successful_concepts: int = 0
    failed_concepts: int = 0
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    duration: float = 0.0
    results: List[LearningMaterialResult] = field(default_factory=list)

    def update_duration(self):
        if self.end_time:
            self.duration = (self.end_time - self.start_time).total_seconds()


# -------------------------------
# Main entry point
# -------------------------------
async def generate_learning_materials(
    file_id: int,
    key_concepts: List[Dict[str, Any]],
    max_retries: int = 2,
) -> LearningMaterialsSummary:
    """
    Generate flashcards, MCQs, and true/false questions for each key concept.
    Returns a summary of successes and failures.
    """
    summary = LearningMaterialsSummary(file_id=file_id, total_concepts=len(key_concepts))

    if not key_concepts:
        logger.warning(f"No key concepts provided for file {file_id}")
        return summary

    logger.info(f"Starting learning materials generation for file {file_id} with {len(key_concepts)} concepts")

    # Process each concept sequentially
    for concept in key_concepts:
        concept_id = concept.get("id")
        concept_title = concept.get("concept_title", "Unnamed Concept")
        concept_results: List[LearningMaterialResult] = []

        logger.info(f"Processing concept {concept_id} ({concept_title})")

        try:
            result = await generate_learning_materials_for_concept(
                concept=concept,
                file_id=file_id,
                max_retries=max_retries,
            )
            concept_results.extend(result)

        except Exception as e:
            logger.error(f"Critical failure processing concept {concept_id}: {e}", exc_info=True)
            concept_results.append(
                LearningMaterialResult(
                    concept_id=concept_id,
                    material_type="all",
                    success=False,
                    error=str(e),
                )
            )

        # Log per-concept summary
        await log_concept_processing_summary(concept_results, file_id)

        # Update global summary
        summary.results.extend(concept_results)
        if any(r.success for r in concept_results):
            summary.successful_concepts += 1
        else:
            summary.failed_concepts += 1

    # Finalize duration
    summary.end_time = datetime.utcnow()
    summary.update_duration()

    logger.info(
        f"Completed learning materials generation for {summary.successful_concepts}/"
        f"{summary.total_concepts} concepts in {summary.duration:.2f}s "
        f"(file_id={file_id})"
    )
    return summary


# -------------------------------
# Per-concept processing
# -------------------------------
async def generate_learning_materials_for_concept(
    concept: Dict[str, Any],
    file_id: int,
    max_retries: int = 2,
) -> List[LearningMaterialResult]:
    """
    Generate flashcards, MCQs, and true/false questions for a single concept.
    Retries failed operations with exponential backoff.
    """
    concept_id = concept["id"]
    results: List[LearningMaterialResult] = []

    # Fetch file once (avoid duplicate DB calls)
    repo_manager = await get_repository_manager()
    file_repo = AsyncFileRepository(repo_manager)
    file_result = await file_repo.get_file_by_id(file_id=file_id)
    user_id = file_result.user_id if file_result else None

    # Define generators using LLM service methods
    generators = {
        "flashcard": llm_service.generate_flashcards,
        "mcq": llm_service.generate_mcqs,
        "true_false": llm_service.generate_true_false_questions,
    }

    # Loop through each type of material
    for material_type, generator in generators.items():
        success = False
        error_msg = None
        retry_count = 0
        start_time = time.time()
        created_count = 0

        for attempt in range(max_retries + 1):
            try:
                # Call the appropriate LLM service method with the required parameters
                if material_type == "flashcard":
                    materials = await generator(
                        concept_title=concept.get("title", ""),
                        concept_explanation=concept.get("explanation", ""),
                        num_flashcards=3,
                    )
                elif material_type == "mcq":
                    materials = await generator(
                        concept_title=concept.get("title", ""),
                        concept_explanation=concept.get("explanation", ""),
                        all_key_concepts=[],  # TODO: Pass actual key concepts for better distractors
                        num_questions=3,
                        num_distractors=3,
                    )
                elif material_type == "true_false":
                    materials = await generator(
                        concept_title=concept.get("title", ""),
                        concept_explanation=concept.get("explanation", ""),
                        all_key_concepts=[],  # TODO: Pass actual key concepts for better false statements
                        num_questions=2,
                    )
                else:
                    materials = []

                created_count = len(materials) if materials else 0
                success = created_count > 0
                break

            except Exception as e:
                retry_count = attempt
                error_msg = str(e)
                logger.warning(
                    f"Attempt {attempt+1}/{max_retries+1} failed for {material_type} "
                    f"(concept_id={concept_id}, file_id={file_id}): {error_msg}"
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt + random.random())

        duration = time.time() - start_time
        results.append(
            LearningMaterialResult(
                concept_id=concept_id,
                material_type=material_type,
                success=success,
                error=error_msg,
                retry_count=retry_count,
                duration=duration,
                created_count=created_count,
            )
        )

    return results


# -------------------------------
# Logging helpers
# -------------------------------
async def log_concept_processing_summary(
    concept_results: List[LearningMaterialResult],
    file_id: int,
):
    """Log a detailed summary of one concept's processing results."""
    for result in concept_results:
        if result.success:
            logger.info(
                f"✅ Created {result.created_count} {result.material_type} items for "
                f"concept {result.concept_id} (file_id={file_id}, "
                f"took {result.duration:.2f}s)"
            )
        else:
            logger.error(
                f"❌ Failed to create {result.material_type} for concept {result.concept_id} "
                f"(file_id={file_id}, retries={result.retry_count}, error={result.error})"
            )
