"""What customers said was wrong, next to what the pipeline did.

    docker exec -w / syntextaiapp-local python /app/api/evals/feedback_report.py
    docker exec -w / syntextaiapp-local python /app/api/evals/feedback_report.py --all
    docker exec -w / syntextaiapp-local python /app/api/evals/feedback_report.py --limit 200

WHY THIS EXISTS

A thumbs-down on its own is a tally: three people were unhappy. Joined to the
run that produced the answer it becomes a diagnosis, because agent_runs already
records what was asked, how many retrievals it took, whether coverage was
satisfied, and how much context the model was handed.

That join is the whole reason for the message_id column added in
20260808_message_feedback. Without it a rating can only be matched to a run by
timestamp inside a conversation, which is a guess.

WHAT TO LOOK FOR

The chip tallies at the bottom say which failure dominates. Each one points
somewhere different, so the tally decides what is worth working on:

    wrong_source        cited the wrong place. Rank or chunk boundaries.
    not_in_documents    answered from the model, not the documents. Grounding.
    incomplete          a second source was needed and did not arrive. Coverage.
    wrong               had the right context and still got it wrong. The model.

`covered: no` alongside a complaint is the strongest single signal here: the
pipeline knew it had not satisfied the question and answered anyway.

DELIBERATELY A CLI, NOT A PAGE

This reads across every tenant, so it must never be served to a browser.
Anything exposing it over HTTP has to answer the reach question first.
"""
import argparse
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, "/app")

from api.models.async_db import get_database_url  # noqa: E402
from api.repositories.repository_manager import RepositoryManager  # noqa: E402


def _wrap(text: str, width: int = 92, indent: str = "    ") -> str:
    """Cheap wrapper. Answers are markdown and long; this is for reading."""
    if not text:
        return f"{indent}(none)"
    words = str(text).split()
    lines, line = [], indent
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = indent + word
        else:
            line = f"{line} {word}" if line.strip() else indent + word
    if line.strip():
        lines.append(line)
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="include thumbs-up too; by default only complaints are shown",
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    store = RepositoryManager(database_url=get_database_url())
    rows = await store.chat_repo.feedback_for_report(
        limit=args.limit, rating=None if args.all else -1
    )

    if not rows:
        print("No feedback yet.")
        print()
        print("Nothing has gone wrong, or nobody has said so. Until a rating")
        print("exists, every claim about answer quality is still our own.")
        return

    up = sum(1 for r in rows if r["rating"] == 1)
    down = sum(1 for r in rows if r["rating"] == -1)
    print(f"{len(rows)} rating(s): {up} up, {down} down")
    print("=" * 96)

    for row in rows:
        run = row.get("run") or {}
        mark = "UP  " if row["rating"] == 1 else "DOWN"
        when = row["created_at"].strftime("%Y-%m-%d %H:%M") if row["created_at"] else "?"

        print()
        print(f"[{mark}] {when}   message {row['message_id']}   workspace {row.get('workspace_id')}")
        if row.get("reason"):
            print(f"  reason: {row['reason']}")
        if row.get("comment"):
            print(f"  said:   {row['comment']}")

        print("  asked:")
        print(_wrap(row.get("question") or "(no run recorded)", indent="    "))
        print("  answered:")
        print(_wrap(row.get("answer"), indent="    "))

        if run:
            needs = run.get("covered_needs") or []
            # The pipeline's own verdict on whether it had what it needed.
            # A complaint on top of "no" is the cheapest lead in the file.
            covered = "yes" if needs else "no"
            print(
                f"  pipeline: retrievals={run.get('retrievals')} "
                f"context_chunks={run.get('context_chunks')} covered={covered} "
                f"mode={run.get('mode')}"
            )
            if needs:
                print(f"    covered_needs: {', '.join(str(n) for n in needs)}")
        else:
            # Not a failure. Runs from before the message link existed, and
            # anything pruned since, have nothing to join to.
            print("  pipeline: no run recorded for this answer")

    complaints = [r for r in rows if r["rating"] == -1]
    if complaints:
        print()
        print("=" * 96)
        print("Chips, most common first:")
        tally = Counter(r["reason"] or "(no chip)" for r in complaints)
        for reason, count in tally.most_common():
            print(f"  {count:3d}  {reason}")

        # Only complaints that actually have a run can say anything about
        # coverage. Counting the ones with no run at all as "uncovered" reads
        # as a pipeline failure when the truth is that the answer predates the
        # message link, which is a different fact and not a lead.
        with_run = [r for r in complaints if r.get("run")]
        unlinked = len(complaints) - len(with_run)

        uncovered = sum(1 for r in with_run if not r["run"].get("covered_needs"))
        if uncovered:
            print()
            print(
                f"  {uncovered} of {len(with_run)} complaints with a run came from one "
                "that had not satisfied the question and answered anyway."
            )
        if unlinked:
            print()
            print(
                f"  {unlinked} complaint(s) have no run to join to, so nothing can be "
                "said about how they were answered. Answers from before "
                "20260808_message_feedback."
            )


if __name__ == "__main__":
    asyncio.run(main())
