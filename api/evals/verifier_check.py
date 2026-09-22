"""Can the verifier's verdicts be trusted? Checked on cases whose answer is known.

    docker exec -w / syntextaiapp-local python /app/api/evals/verifier_check.py
    RUNS=3 VERIFIER_MODEL=... docker exec -w / -e RUNS -e VERIFIER_MODEL syntextaiapp-local \
        python /app/api/evals/verifier_check.py

An instrument is checked before anything it says is believed. For every
benchmark question whose required facts can be found verbatim in a chunk on the
page the benchmark says holds them, the verifier is asked about three claims:

    right page     the facts, citing the chunk that states them    -> SUPPORTED
    wrong page     the same facts, citing another chunk of the SAME document,
                   the one that best matches the question's words   -> NOT SUPPORTED
    wrong value    the benchmark's must_not_include value (the neighbouring
                   table cell, usually), citing the RIGHT chunk     -> NOT SUPPORTED

The wrong page is the hard negative on purpose: same manual, same vocabulary,
different page. The wrong value is harder still, because the passage it cites
often contains that value too, in the row above.

No retrieval and no answer model: the only thing under test is the verdict.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import yaml
from sqlalchemy import text

from api.agents import verifier
from api.repositories.repository_manager import RepositoryManager
from api.services.llm_service import aclose_client

HERE = Path(__file__).resolve().parent
SPECS = [(HERE / "citation_benchmark.yaml", int(os.getenv("WS", "4219"))),
         (HERE / "hvac_benchmark.yaml", int(os.getenv("HVAC_WS", "7354")))]
RUNS = int(os.getenv("RUNS", "1"))

CHUNKS = """
SELECT c.id, c.content, s.page_number, f.file_name
FROM chunks c JOIN files f ON f.id = c.file_id LEFT JOIN segments s ON s.id = c.segment_id
WHERE f.workspace_id = :w AND f.file_name = :f
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _first(values):
    if not values:
        return None
    v = values[0]
    return v[0] if isinstance(v, list) else v


def _overlap(a: str, b: str) -> int:
    return len(verifier._terms(a) & verifier._terms(b))


async def build_cases(session):
    cases, skipped = [], 0
    for spec, ws in SPECS:
        for q in (yaml.safe_load(spec.read_text()) or {}).get("questions", []):
            facts = q.get("must_include") or []
            cites = q.get("citations") or []
            if not facts or len(cites) != 1:
                skipped += 1
                continue
            file, pages = cites[0]["file"], set(cites[0]["pages"])
            rows = (await session.execute(text(CHUNKS), {"w": ws, "f": file})).all()
            # A fact may be a list of accepted spellings; any one will do.
            alts = [f if isinstance(f, list) else [f] for f in facts]
            # Whole words only. As a substring, the SEER fact "17" matched a
            # part number, 170100, and the verifier was scored wrong for
            # correctly rejecting a page that does not say 17.
            has = lambda r, a: next(
                (x for x in a if re.search(rf"(?<!\w){re.escape(_norm(str(x)))}(?!\w)", _norm(r.content))),
                None,
            )
            right = [r for r in rows if r.page_number in pages
                     and all(has(r, a) for a in alts)]
            if not right:
                skipped += 1
                continue
            # Of the chunks on the right page that hold the facts, the one that
            # best matches the question. A bare fact like "records" occurs in
            # passages that have nothing to do with the question, and taking
            # the first such chunk tested the verifier on a citation a careful
            # reader would also reject.
            right.sort(key=lambda r: _overlap(q["question"], r.content), reverse=True)
            facts = [has(right[0], a) for a in alts]
            others = [r for r in rows if r.page_number not in pages
                      and not any(has(r, a) for a in alts)]
            if not others:
                skipped += 1
                continue
            wrong = max(others, key=lambda r: _overlap(q["question"], r.content))
            cases.append({
                "id": f"{spec.stem.split('_')[0]}-{q['id']}",
                "question": q["question"],
                "facts": facts,
                "wrong_value": _first(q.get("must_not_include")),
                "right": dict(right[0]._mapping),
                "wrong": dict(wrong._mapping),
            })
    return cases, skipped


def claims_for(case):
    stmt = f'Asked "{case["question"]}", the documents say: {", ".join(case["facts"])}.'
    seg = lambda r: {"file_name": r["file_name"], "page_number": r["page_number"], "content": r["content"]}
    segments = [seg(case["right"]), seg(case["wrong"])]
    claims = [("right page", True, verifier.Claim(0, 0, stmt, [1])),
              ("wrong page", False, verifier.Claim(0, 0, stmt, [2]))]
    if case["wrong_value"]:
        bad = f'Asked "{case["question"]}", the documents say: {case["wrong_value"]}.'
        claims.append(("wrong value", False, verifier.Claim(0, 0, bad, [1])))
    return segments, claims


async def main():
    repo = RepositoryManager().file_repo
    async with repo.get_async_session() as session:
        cases, skipped = await build_cases(session)
    print(f"model={verifier.VERIFIER_MODEL} effort={verifier.VERIFIER_EFFORT} "
          f"cases={len(cases)} skipped={skipped} runs={RUNS}\n")

    totals = {}
    for run in range(1, RUNS + 1):
        tally = {}
        for case in cases:
            segments, claims = claims_for(case)
            numbered = [(i, c) for i, (_, _, c) in enumerate(claims, start=1)]
            reply = await verifier._ask(verifier._check_prompt(numbered, segments))
            got = {int(n): v.upper().startswith("SUPPORTED")
                   for n, v in verifier._VERDICT_RE.findall(reply or "")}
            for i, (kind, expect, _) in enumerate(claims, start=1):
                t = tally.setdefault(kind, [0, 0, 0])  # right, wrong, unparsed
                if i not in got:
                    t[2] += 1
                elif got[i] == expect:
                    t[0] += 1
                else:
                    t[1] += 1
                    if RUNS == 1:
                        print(f"  miss {case['id']:<12} {kind:<12} said "
                              f"{'SUPPORTED' if got[i] else 'NOT SUPPORTED'}")
        line = "  ".join(f"{k} {v[0]}/{sum(v)}" + (f" ({v[2]} unparsed)" if v[2] else "")
                         for k, v in tally.items())
        print(f"run {run}: {line}")
        for k, v in tally.items():
            totals.setdefault(k, []).append(v[0])
    if RUNS > 1:
        for k, v in totals.items():
            print(f"{k:<12} mean {sum(v)/len(v):.1f}  range {min(v)}-{max(v)}")
    await aclose_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
