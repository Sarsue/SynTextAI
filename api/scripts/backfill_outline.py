"""Extract the table of contents from documents ingested before we kept it.

Every PDF already in a workspace was opened, read for its text, and closed
without anyone asking what it contained. This fetches each one back from
storage and extracts only the outline: no re-chunking, no re-embedding, no new
file ids, so nothing a past answer cited moves.

    docker exec -w /app syntextaiapp-local python -m api.scripts.backfill_outline --workspace 4219
    docker exec -w /app syntextaiapp-local python -m api.scripts.backfill_outline --all --dry-run

Safe to re-run: documents that already have an outline are skipped unless
--force. A document whose outline comes back empty is recorded as attempted so
a rerun does not download it again for nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_outline")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="redo documents that already have one")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.workspace and not args.all:
        ap.error("pass --workspace N or --all")

    from api.core.utils import download_from_gcs
    from api.repositories.repository_manager import RepositoryManager
    from api.services.outline import extract_pdf_outline

    repo = RepositoryManager().file_repo
    where = "TRUE" if args.all else "f.workspace_id = :ws"
    skip = "" if args.force else " AND f.outline IS NULL"

    async with repo.get_async_session() as session:
        rows = (await session.execute(
            text(f"""
                SELECT f.id, f.file_name, f.file_url
                FROM files f
                WHERE {where}{skip} AND lower(f.file_name) LIKE '%.pdf'
                ORDER BY f.id
            """),
            {"ws": args.workspace},
        )).all()

    if not rows:
        logger.info("Nothing to do.")
        return 0

    logger.info(f"{len(rows)} document(s)")
    if args.dry_run:
        for fid, name, _ in rows:
            logger.info(f"  {fid:>5}  {name}")
        return 0

    done = 0
    for fid, name, url in rows:
        if not url:
            logger.warning(f"  {name}: no stored URL, skipping")
            continue
        try:
            data = await asyncio.to_thread(download_from_gcs, url)
        except Exception as e:
            logger.warning(f"  {name}: could not fetch ({e})")
            continue
        if not data:
            logger.warning(f"  {name}: empty download")
            continue

        entries = await asyncio.to_thread(extract_pdf_outline, data)
        # An empty list is still written, so a rerun does not download this
        # document again to learn the same thing.
        await repo.set_outline(int(fid), entries)
        done += 1
        logger.info(f"  {name}: {len(entries)} outline entries")

    logger.info(f"Done. {done} document(s) processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
