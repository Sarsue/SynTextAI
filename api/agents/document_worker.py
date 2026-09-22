"""A document worker: answer the question from ONE document, and nothing else.

WHY ONE DOCUMENT PER WORKER

Every measurement on this model points the same way: it does worse with more
text in one prompt and better when each prompt is aimed at one thing. top_k 40
put two more correct sources in front of it and scored lower than top_k 25.
A search loop aimed at a second information need scored higher. A worker is the
same lever pushed further: its search is confined to one document, so a manual
that is ranked twelfth across the workspace is ranked first inside itself, and
the answer it writes is never distracted by four similar manuals.

WHAT IT RETURNS

A Draft from the same compose step the single-document path uses, so the
grounding rules, the refusal word and the citation markers are identical and
nothing about writing an answer exists twice. A worker whose document does not
answer returns the refusal, and the writer leaves it out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from api.agents.evidence import EvidenceSet
from api.agents.models import WORKER_MODEL
from api.rag.chunk_selector import SmartChunkSelector
from api.services.llm_service import MAX_TOKENS_CONTEXT, get_text_embedding
from api.services.answer_composer import Draft

logger = logging.getLogger(__name__)

# Passages one worker searches for inside its document. Fewer than the broad
# search's 25 because the pool is one document, not a workspace.
WORKER_TOP_K = 12

# A worker's share of the context. Smaller than the single path's on purpose:
# a small prompt aimed at one document is the whole reason the worker exists.
WORKER_TOKEN_BUDGET = max(2000, int(MAX_TOKENS_CONTEXT * 0.1))

_selector = SmartChunkSelector()


@dataclass
class WorkerResult:
    file_id: int
    file_name: str
    focus: str
    draft: Draft
    chunks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.draft.kind in ("answer", "uncited")


async def answer_from_document(
    *,
    store: Any,
    composer: Any,
    user_id: int,
    workspace_id: Optional[int],
    accessible_workspace_ids: Optional[List[int]],
    file_id: int,
    file_name: str,
    question: str,
    focus: str,
    formatted_history: str,
    language: str,
    comprehension_level: str,
    seed: List[Dict[str, Any]],
) -> WorkerResult:
    # The passages the broad search already found in this document count as
    # one search, and the worker's own search, aimed at its focus, as another.
    # The evidence set fuses them by rank, as it does any two retrievals.
    evidence = EvidenceSet()
    evidence.add(seed, question)
    try:
        query = focus if focus and focus != question else question
        emb = await get_text_embedding(query)
        found = await store.file_repo.hybrid_search(
            user_id=user_id,
            query=query,
            query_embedding=emb,
            workspace_id=workspace_id,
            file_id=file_id,
            top_k=WORKER_TOP_K,
            accessible_workspace_ids=accessible_workspace_ids,
        )
        evidence.add(found or [], query)
    except Exception as e:
        # The seed alone is still this document's evidence.
        logger.warning({"event": "document_worker.search_failed", "file_id": file_id,
                        "error": str(e)[:300]})

    chunks = _selector.select(evidence.as_chunks(), question, token_budget=WORKER_TOKEN_BUDGET)
    asked = question if not focus or focus == question else (
        f"{question}\n\n(From this document, find: {focus}. Other documents cover "
        "the rest, so answer only what this one says.)"
    )
    draft = await composer.compose(
        asked, formatted_history, chunks, language, comprehension_level, model=WORKER_MODEL,
    )
    logger.info({"event": "document_worker.done", "file_id": file_id,
                 "kind": draft.kind, "chunks": len(chunks)})
    return WorkerResult(file_id, file_name, focus, draft, chunks)
