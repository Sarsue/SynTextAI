"""Show what the agent actually did, per question.

A benchmark score says a question failed. It cannot say which of these went
wrong, and they need four different fixes:

    the query was bad            it searched for the wrong thing
    retrieval was fine           the right page came back and the answer
                                 ignored it
    it stopped too early         it had two of the three parts and finished
    it went in circles           three searches returning the same pages

Three wrong conclusions were drawn in one day from having only the final answer
to look at. This reads the traces the worker stores on agent_runs.result and
lines them up against the benchmark's own verdicts.

    docker exec -w /app syntextaiapp-local python -m api.scripts.show_traces \\
        --results api/evals/results/20260805-143948-tools-temp01-x4.json

    docker exec -w /app syntextaiapp-local python -m api.scripts.show_traces --last 10

With --results it shows only the questions that failed, which is normally what
is wanted. Pass --all for everything.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text


def _fmt_step(step: Dict[str, Any]) -> str:
    args = step.get("args") or {}
    label = args.get("query") or args.get("file_name") or ""
    if args.get("file_name") and args.get("query"):
        label = f"{args['query']}  (in {args['file_name']})"
    if args.get("page"):
        label = f"{args.get('file_name')} p{args['page']}"
    head = f"    {step['step']}. {step['tool']}({label})"
    returned = step.get("returned") or []
    new = step.get("new_pages") or []
    if not returned:
        return head + (f"  -> {step.get('note') or 'nothing'}")
    # Only the new pages are interesting; the rest it had already seen.
    shown = ", ".join(new[:6]) or "nothing new"
    extra = f", {len(returned) - len(new)} already seen" if len(returned) > len(new) else ""
    return head + f"\n         -> {shown}{extra}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, help="a benchmark results json")
    ap.add_argument("--last", type=int, help="the most recent N query runs")
    ap.add_argument("--all", action="store_true", help="passes as well as failures")
    args = ap.parse_args()
    if not args.results and not args.last:
        ap.error("pass --results FILE or --last N")

    from api.repositories.repository_manager import RepositoryManager
    repo = RepositoryManager().file_repo

    wanted: Dict[int, Dict[str, Any]] = {}
    if args.results:
        data = json.loads(args.results.read_text())
        for run in (data.get("runs") or [data.get("results") or []]):
            for r in run:
                if r.get("history_id") and (args.all or not r.get("passed")):
                    wanted[r["history_id"]] = r

    async with repo.get_async_session() as session:
        if wanted:
            rows = (await session.execute(
                text("""SELECT chat_history_id, result FROM agent_runs
                        WHERE chat_history_id = ANY(:ids) AND result IS NOT NULL
                        ORDER BY created_at"""),
                {"ids": list(wanted.keys())},
            )).all()
        else:
            rows = (await session.execute(
                text("""SELECT chat_history_id, result FROM agent_runs
                        WHERE run_type = 'answer_query' AND result IS NOT NULL
                        ORDER BY created_at DESC LIMIT :n"""),
                {"n": args.last},
            )).all()

    if not rows:
        print("No traces found. The worker stores them on agent_runs.result; "
              "runs from before that was added have none.")
        return 0

    for history_id, result in rows:
        q = wanted.get(history_id, {})
        print("=" * 74)
        print(f"q{q.get('id', '?')}  {q.get('question', f'history {history_id}')}")
        if q.get("failures"):
            print(f"  VERDICT: {q['failures'][0][:70]}")

        trace = (result or {}).get("trace") or []
        if not trace:
            print(f"  (no trace; mode={result.get('mode')})")
            continue
        for step in trace:
            print(_fmt_step(step))

        d = (result or {}).get("diagnosis") or {}
        if d:
            print(f"  saw {d.get('pages_seen')} pages across "
                  f"{d.get('documents_touched')} documents, "
                  f"cited {d.get('pages_cited')} across {d.get('documents_cited')}"
                  + (f", {d['repeat_searches']} search(es) returned nothing new"
                     if d.get("repeat_searches") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
