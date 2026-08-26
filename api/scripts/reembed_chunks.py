"""Find and repair chunks whose vectors were written by a different embedding model.

THE FAILURE THIS EXISTS FOR

A vector only means anything next to other vectors from the same model. Embed
the documents with one model and the questions with another and nothing errors:
the distance calculation is perfectly happy, the numbers come back, and the
answer cites confidently from whatever happened to land nearest. The provider
consolidation note in docs/ENGINEERING_OVERVIEW.md predicted this in as many
words. It then happened.

Measured 2026-08-15 on the local database, one chunk per workspace, comparing
each stored vector against a fresh embedding of that chunk's own text:

    workspace   chunks   uploaded                 cosine
        1          184   2026-08-08 to 08-12      -0.044   wrong model
     4060          210   2026-08-06 to 08-07      -0.044   wrong model
     4219          540   2026-08-06               -0.053   wrong model
     7354         1607   2026-08-12               +0.9998  fine

Anything ingested before the move from voyage-3.5-lite to Qwen3-Embedding-0.6B
holds Voyage vectors. Same 1024 dimensions, so nothing complains. On the SMB
benchmark corpus in workspace 4219 the effect is that 11 of 17 single-document
questions no longer retrieve their source at all.

HOW THE CHECK WORKS, AND WHY IT IS THE RIGHT SHAPE OF QUESTION

Embedding is a pure function of its input, so a chunk's stored vector and a
fresh embedding of its own text should be the same vector. Cosine near 1 means
the same model wrote it; cosine near 0 means a different one did. That is a
deterministic question with a deterministic answer, and it costs one embedding
call per workspace to ask. It does not need a benchmark run and it does not
need anybody's opinion.

    docker exec -w /app syntextaiapp-local python -m api.scripts.reembed_chunks --check
    docker exec -w /app syntextaiapp-local python -m api.scripts.reembed_chunks --workspace 4219
    docker exec -w /app syntextaiapp-local python -m api.scripts.reembed_chunks --all --dry-run

WHAT THE REPAIR DOES

Re-embeds every chunk with the model configured right now, and rewrites
`content_hash` to match. The hash is the sha256 of the text that was actually
embedded, and it is what lets a later upload reuse a vector instead of paying
for it again. Leaving a stale hash beside a new vector poisons that cache for
every document that shares the text.

A chunk is re-embedded from its own text, which is exactly what ingestion
embeds, so a repaired corpus is indistinguishable from a freshly uploaded one.

Chunks are read and written in batches so an interrupted run keeps the work it
finished. Re-running is safe and cheap: the check skips workspaces already
holding vectors from the current model.
"""
from __future__ import annotations

import argparse

from api.services.llm_service import MODEL_EMBEDDING_ID
import asyncio
import hashlib
import logging
from typing import List, Optional, Tuple

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("reembed")

# One database round trip and one embedding call per batch.
BATCH = 100

# Above this, the stored vector and a fresh one are the same vector and the
# difference is floating point. Below it they are unrelated. There is no
# meaningful middle: a different model scores near zero, not near 0.9.
SAME_MODEL = 0.95


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _parse_vector(stored) -> List[float]:
    return [float(x) for x in str(stored).strip("[]").split(",") if x.strip()]


def _vector_literal(embedding) -> str:
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"


async def _sample_cosine(repo, workspace_id: int, get_text_embedding) -> Optional[float]:
    """How well one chunk of this workspace matches a fresh embedding of itself."""
    async with repo.get_async_session() as session:
        row = (await session.execute(
            text(
                """
                SELECT c.content, c.embedding
                FROM chunks c JOIN files f ON f.id = c.file_id
                WHERE f.workspace_id = :w AND c.embedding IS NOT NULL
                  AND length(c.content) > 150
                ORDER BY c.id LIMIT 1
                """
            ),
            {"w": workspace_id},
        )).first()

    if not row:
        return None
    content, stored = row
    fresh = await get_text_embedding(content)
    stored = _parse_vector(stored)
    if len(stored) != len(fresh):
        # A dimension change is the same problem, louder.
        return 0.0
    return _cosine(stored, fresh)


async def _workspaces(repo) -> List[Tuple[int, int, int]]:
    """Each workspace with (chunks, chunks that cannot be repaired).

    A chunk with no `content` cannot be re-embedded, because the text it was
    made from is not stored anywhere: `chunks.content` only exists from
    2026-08-06. Those rows are also invisible to both keyword arms, whose
    tsvector is built from that same column. They are unreachable except by a
    vector nothing can rewrite, so they are counted and reported rather than
    quietly skipped, which is what "88 of 210 re-embedded" would otherwise be.
    """
    async with repo.get_async_session() as session:
        return [
            (int(ws), int(n), int(dead))
            for ws, n, dead in (await session.execute(
                text(
                    """
                    SELECT f.workspace_id, count(c.id),
                           count(c.id) FILTER (WHERE c.content IS NULL)
                    FROM files f JOIN chunks c ON c.file_id = f.id
                    WHERE f.workspace_id IS NOT NULL
                    GROUP BY 1 ORDER BY 1
                    """
                )
            )).all()
        ]


async def _repair(repo, workspace_id: Optional[int], dry_run: bool,
                  embed_batches) -> int:
    """Re-embed every chunk in scope. Returns how many were rewritten."""
    where = "f.workspace_id = :w" if workspace_id else "TRUE"

    async with repo.get_async_session() as session:
        total = (await session.execute(
            text(
                f"""
                SELECT count(c.id) FROM chunks c JOIN files f ON f.id = c.file_id
                WHERE {where} AND c.content IS NOT NULL
                """
            ),
            {"w": workspace_id},
        )).scalar() or 0

    if not total:
        logger.info("  nothing to re-embed")
        return 0
    logger.info(f"  {total} chunk(s) to re-embed")
    if dry_run:
        return 0

    done = 0
    last_id = 0
    while True:
        async with repo.get_async_session() as session:
            rows = (await session.execute(
                text(
                    f"""
                    SELECT c.id, c.content
                    FROM chunks c JOIN files f ON f.id = c.file_id
                    WHERE {where} AND c.content IS NOT NULL AND c.id > :after
                    ORDER BY c.id LIMIT :batch
                    """
                ),
                {"w": workspace_id, "after": last_id, "batch": BATCH},
            )).all()

        if not rows:
            break

        texts = [content or "" for _cid, content in rows]
        embeddings = await embed_batches(texts, batch_size=50)
        if len(embeddings) != len(rows):
            # Writing a partial batch would leave the corpus in two states at
            # once, which is worse than stopping with a clear message.
            logger.error(
                f"  embedding count mismatch ({len(embeddings)} for {len(rows)}); "
                f"stopping after {done} chunks"
            )
            return done

        async with repo.get_async_session() as session:
            for (cid, _content), embedded, emb in zip(rows, texts, embeddings):
                await session.execute(
                    text(
                        """
                        UPDATE chunks
                        SET embedding = CAST(:emb AS vector),
                            content_hash = :hash,
                            -- Stamped on repair, so a row this script has
                            -- touched stops being an unknown. The column is
                            -- new and almost every existing row is NULL, which
                            -- the product treats as "not measured" rather than
                            -- "stale": counting unknowns as stale would put a
                            -- warning on nearly every document somebody owns.
                            -- They resolve as they are repaired.
                            embedding_model = :model
                        WHERE id = :cid
                        """
                    ),
                    {
                        "emb": _vector_literal(emb),
                        "hash": hashlib.sha256(embedded.encode("utf-8")).hexdigest(),
                        "model": MODEL_EMBEDDING_ID,
                        "cid": cid,
                    },
                )
            await session.commit()

        done += len(rows)
        last_id = rows[-1][0]
        logger.info(f"  {done}/{total}")

    return done


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report which workspaces hold foreign vectors, change nothing")
    ap.add_argument("--workspace", type=int, help="repair this workspace")
    ap.add_argument("--all", action="store_true", help="repair every stale workspace")
    ap.add_argument("--force", action="store_true",
                    help="repair even a workspace the check says is fine")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    args = ap.parse_args()

    if not (args.check or args.workspace or args.all):
        ap.error("pass --check, --workspace N, or --all")

    from api.repositories.repository_manager import RepositoryManager
    from api.services.llm_service import (
        aclose_client,
        get_text_embedding,
        get_text_embeddings_in_batches,
    )

    repo = RepositoryManager().file_repo
    rows = await _workspaces(repo)
    if not rows:
        logger.info("No workspace has any chunks.")
        await aclose_client()
        return 0

    logger.info(f"{'workspace':>10} {'chunks':>8} {'no text':>8}  {'cosine':>8}  state")
    stale: List[int] = []
    unrepairable = 0
    for ws, n, dead in rows:
        if args.workspace and ws != args.workspace:
            continue
        unrepairable += dead
        cos = await _sample_cosine(repo, ws, get_text_embedding)
        if cos is None:
            logger.info(f"{ws:>10} {n:>8} {dead:>8}  {'-':>8}  no chunk long enough to sample")
            continue
        ok = cos > SAME_MODEL
        if not ok:
            stale.append(ws)
        logger.info(
            f"{ws:>10} {n:>8} {dead:>8}  {cos:>+8.4f}  "
            f"{'current model' if ok else 'WRITTEN BY ANOTHER MODEL'}"
        )

    if unrepairable:
        logger.warning(
            f"\n{unrepairable} chunk(s) have no stored text. They cannot be "
            f"re-embedded and no keyword search can reach them. Their documents "
            f"have to be uploaded again to be searchable."
        )

    if args.check:
        logger.info(
            f"\n{len(stale)} workspace(s) need re-embedding"
            + (f": {stale}" if stale else "")
        )
        await aclose_client()
        return 0

    targets = [args.workspace] if args.workspace else stale
    if args.workspace and args.workspace not in stale and not args.force:
        logger.info("\nThis workspace already holds vectors from the current model. "
                    "Pass --force to re-embed it anyway.")
        await aclose_client()
        return 0
    if not targets:
        logger.info("\nEvery workspace already holds vectors from the current model.")
        await aclose_client()
        return 0

    total = 0
    for ws in targets:
        logger.info(f"\nworkspace {ws}")
        total += await _repair(repo, ws, args.dry_run, get_text_embeddings_in_batches)

    logger.info(f"\nDone. {total} chunk(s) re-embedded.")
    await aclose_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
