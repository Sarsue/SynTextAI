"""Add chunk context to documents that were ingested before it existed.

Contextual retrieval only helps a document that has it, and every document
uploaded before services/contextualizer.py existed has none. Re-uploading is
not an option: the customer would lose their file ids, and every citation in
every past answer points at those.

So this walks the existing segments, writes a context sentence for each, and
re-embeds the chunk from the context plus the text. Re-embedding is the part
that matters. Writing context without it updates the keyword half of hybrid
search and leaves the vector half exactly as blind as it was, which would show
up as a small improvement and hide the fact that most of the work did nothing.

    docker exec -w /app syntextaiapp-local python -m api.scripts.backfill_context --workspace 4219
    docker exec -w /app syntextaiapp-local python -m api.scripts.backfill_context --all --dry-run

Safe to re-run. Segments that already have context are skipped unless --force,
so an interrupted run resumes rather than starting again and paying twice.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any, Dict, List

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_context")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=int, help="only this workspace")
    ap.add_argument("--all", action="store_true", help="every workspace")
    ap.add_argument("--force", action="store_true", help="redo segments that already have context")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    ap.add_argument(
        "--strip", action="store_true",
        help=("Undo it: clear context and re-embed from the text alone. Exists so "
              "the benefit can be measured as a difference rather than asserted. "
              "A change to ingestion that cannot be turned off cannot be tested."),
    )
    args = ap.parse_args()

    if not args.workspace and not args.all:
        ap.error("pass --workspace N or --all")

    # Contextualisation is off by default in the running service; a backfill is
    # an explicit instruction to do it, so turn it on for this process only.
    os.environ["CONTEXTUALIZE_CHUNKS"] = "true"

    from api.repositories.repository_manager import RepositoryManager
    from api.services.contextualizer import add_context, embedding_text
    from api.services.llm_service import aclose_client, get_text_embeddings_in_batches

    store = RepositoryManager()
    repo = store.file_repo

    if args.strip:
        return await _strip(repo, args, get_text_embeddings_in_batches, aclose_client)

    where = "TRUE" if args.all else "f.workspace_id = :ws"
    skip = "" if args.force else " AND s.context IS NULL"

    async with repo.get_async_session() as session:
        files = (await session.execute(
            text(f"""
                SELECT f.id, f.file_name, count(s.id) AS segments
                FROM files f JOIN segments s ON s.file_id = f.id
                WHERE {where}{skip}
                GROUP BY f.id, f.file_name ORDER BY f.id
            """),
            {"ws": args.workspace},
        )).all()

    if not files:
        logger.info("Nothing to do.")
        return 0

    logger.info(f"{len(files)} document(s), {sum(f[2] for f in files)} segment(s) to contextualise")
    for fid, name, count in files:
        logger.info(f"  {fid:>5}  {name[:52]:54} {count:>4} segments")
    if args.dry_run:
        logger.info("Dry run; nothing written.")
        return 0

    total = 0
    for fid, name, _count in files:
        async with repo.get_async_session() as session:
            rows = (await session.execute(
                text(f"""
                    SELECT s.id, s.content, s.page_number
                    FROM segments s WHERE s.file_id = :fid{skip.replace(' AND s.', ' AND s.')}
                    ORDER BY s.page_number, s.id
                """),
                {"fid": fid},
            )).all()

        units: List[Dict[str, Any]] = [
            {"segment_id": r[0], "text": r[1] or "", "page_num": r[2]} for r in rows
        ]
        if not units:
            continue

        written = await add_context(units, name)
        if not written:
            logger.warning(f"  {name}: no context produced, leaving as it was")
            continue

        # Re-embed from context + text. Without this the vector half of hybrid
        # search never learns anything the backfill wrote.
        embeddings = await get_text_embeddings_in_batches(
            [embedding_text(u) for u in units], batch_size=50
        )
        if len(embeddings) != len(units):
            logger.error(f"  {name}: embedding count mismatch, skipping this document")
            continue

        async with repo.get_async_session() as session:
            for unit, emb in zip(units, embeddings):
                ctx = unit.get("context")
                if not ctx:
                    continue
                await session.execute(
                    text("UPDATE segments SET context = :ctx WHERE id = :sid"),
                    {"ctx": ctx, "sid": unit["segment_id"]},
                )
                await session.execute(
                    text("UPDATE chunks SET embedding = CAST(:emb AS vector) WHERE segment_id = :sid"),
                    {"emb": "[" + ",".join(str(float(x)) for x in emb) + "]",
                     "sid": unit["segment_id"]},
                )
            await session.commit()

        total += written
        logger.info(f"  {name}: {written} segments contextualised and re-embedded")

    logger.info(f"Done. {total} segments updated.")
    await aclose_client()
    return 0


async def _strip(repo, args, embed_batches, aclose) -> int:
    """Clear context and re-embed from content alone, restoring the prior state."""
    where = "TRUE" if args.all else "f.workspace_id = :ws"
    async with repo.get_async_session() as session:
        rows = (await session.execute(
            text(f"""
                SELECT s.id, s.content FROM segments s JOIN files f ON f.id = s.file_id
                WHERE {where} AND s.context IS NOT NULL ORDER BY s.id
            """),
            {"ws": args.workspace},
        )).all()

    if not rows:
        logger.info("Nothing to strip.")
        return 0
    logger.info(f"Stripping context from {len(rows)} segments and re-embedding from text alone")
    if args.dry_run:
        return 0

    embeddings = await embed_batches([r[1] or "" for r in rows], batch_size=50)
    async with repo.get_async_session() as session:
        for (sid, _content), emb in zip(rows, embeddings):
            await session.execute(
                text("UPDATE segments SET context = NULL WHERE id = :sid"), {"sid": sid})
            await session.execute(
                text("UPDATE chunks SET embedding = CAST(:emb AS vector) WHERE segment_id = :sid"),
                {"emb": "[" + ",".join(str(float(x)) for x in emb) + "]", "sid": sid})
        await session.commit()
    logger.info(f"Stripped {len(rows)} segments.")
    await aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
