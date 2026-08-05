"""Where the page that answers the question actually died.

The benchmark knows the correct page for every question. The traces know how
far every retrieved page got. Putting them together turns "the score went down"
into exactly one of four statements:

    never retrieved          retrieval never found it
    retrieved, rejected      the classifier threw it away
    selected, not cited      the answer step ignored it
    cited                    it worked

Without this, a change that turns "cited the wrong page" into "rejected the
right page" reads as no change at all: both leave the page uncited and both
leave the score identical.

    docker exec -w /app syntextaiapp-local python -m api.scripts.gold_recall \\
        --results api/evals/results/20260805-185334-tools-selector-x4.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

import yaml
from sqlalchemy import text

SPEC = Path("/app/api/evals/citation_benchmark.yaml")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--question", type=int, help="show every gold page for one question")
    args = ap.parse_args()

    from api.repositories.repository_manager import RepositoryManager

    data = json.loads(args.results.read_text())
    spec = {q["id"]: q for q in yaml.safe_load(SPEC.read_text())["questions"]}
    repo = RepositoryManager().file_repo

    runs = data.get("runs") or [data["results"]]
    ids = [r["history_id"] for run in runs for r in run if r.get("history_id")]
    async with repo.get_async_session() as session:
        rows = (await session.execute(
            text("SELECT chat_history_id, result FROM agent_runs WHERE chat_history_id = ANY(:ids)"),
            {"ids": ids},
        )).all()
    stored = {h: (r or {}) for h, r in rows}

    verdicts: Counter = Counter()
    per_question: dict = {}
    for run in runs:
        for r in run:
            q = spec.get(r["id"])
            if not q or not q.get("citations"):
                continue
            pages = (((stored.get(r.get("history_id")) or {}).get("diagnosis") or {})
                     .get("lifecycle") or {}).get("pages") or {}
            if not pages:
                continue
            for c in q["citations"]:
                for page in c["pages"]:
                    key = f"{c['file']} p{page}"
                    info = pages.get(key)
                    if info is None:
                        v = "never retrieved"
                    elif info["stage"] == "cited":
                        v = "cited"
                    elif info["stage"] in ("selected", "used", "verified"):
                        v = "selected, not cited"
                    else:
                        v = f"retrieved, rejected: {info.get('why') or 'unknown'}"
                    verdicts[v] += 1
                    per_question.setdefault(r["id"], Counter())[v] += 1
                    break  # one acceptable page per required source is enough

    total = sum(verdicts.values()) or 1
    print(f"Every required page, across {len(runs)} run(s):\n")
    for v, n in verdicts.most_common():
        print(f"   {n:>4}  ({100 * n / total:>4.0f}%)  {v}")

    print("\nBy question (only those that ever missed):\n")
    for qid in sorted(per_question):
        c = per_question[qid]
        if c.get("cited", 0) == sum(c.values()):
            continue
        worst = ", ".join(f"{n}x {v}" for v, n in c.most_common())
        print(f"   q{qid:<3} {spec[qid]['question'][:44]:46} {worst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
