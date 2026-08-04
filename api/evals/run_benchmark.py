"""Run the citation benchmark against a live SyntextAI instance.

Asks every question through the real API, as a real signed-in user, against a
real workspace. Not a unit test of the retriever: the whole path runs, including
ingestion, chunking, search, the model, and the citation rewriting that turns
internal segment markers into a document and a page. That last step is what the
customer actually sees, so it is what gets scored.

    python api/evals/run_benchmark.py \
        --base-url http://127.0.0.1:3000 \
        --token "$ID_TOKEN" \
        --workspace 1 \
        --corpus /path/to/corpus \
        --label baseline

Capture a baseline BEFORE changing retrieval, the agent loop, or the model.
Every later run is only meaningful as a diff against one.

SCORING, IN THE ORDER THAT MATTERS

    cited pages   Did the answer point at a page that holds the answer?
    content       Did the required facts appear?
    refusals      Did it decline when the documents do not say?

A question passes only if every part passes. Partial credit is reported so a
regression can be read at a glance, but it does not soften the verdict: a
prettier answer citing the wrong page is a regression.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests
import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_SPEC = HERE / "citation_benchmark.yaml"
RESULTS_DIR = HERE / "results"

# A cited source reaches the customer as "name.pdf (Page 12)", linked to a URL
# ending "#page=12". Read the link, because the visible text is what a person
# checks and the fragment is where the link actually lands. They should agree;
# if they ever stop agreeing, that is itself worth failing on.
CITATION_RE = re.compile(r"\[([^\]]*?\.(?:pdf|docx|txt|md))\s*\(Page\s+(\d+)\)\]\(([^)]+)\)", re.I)
# Same thing without the markdown wrapper, for answers that inline the label.
# No spaces in the filename class: with them, "see report.pdf (Page 9)" parsed
# the file as "see report.pdf" and every citation silently failed to match.
BARE_RE = re.compile(r"([\w\-.]+\.(?:pdf|docx|txt|md))\s*\(Page\s+(\d+)\)", re.I)

# Phrases that mean "the documents do not answer this". Deliberately about
# absence of grounding, not politeness: "I'm not sure" while still asserting a
# figure is not a refusal.
REFUSAL_MARKERS = (
    "do not contain", "don't contain", "does not contain", "doesn't contain",
    "not mentioned", "no mention", "not covered",
    "not specified", "not addressed", "could not find", "couldn't find",
    "do not cover", "don't cover", "does not cover", "doesn't cover",
    "no information", "not provide", "unable to find", "not found in",
    "outside the scope", "do not say", "don't say", "does not say",
)


def normalise(text: str) -> str:
    """Whitespace-insensitive comparison text.

    PDFs are full of non-breaking spaces: the OSHA handbook writes
    "24\\xa0hours", so a naive substring check for "24 hours" fails on a page
    that plainly says it. The model will write an ordinary space, so compare on
    normalised text rather than making every question encode the PDF's quirks.
    """
    return re.sub(r"\s+", " ", text or "").lower()


def parse_citations(answer: str) -> List[Tuple[str, int]]:
    """Every (file, page) the answer points at, in order of first appearance."""
    found: List[Tuple[str, int]] = []
    seen = set()

    for name, page, target in CITATION_RE.findall(answer):
        # Prefer the link fragment; it is where the citation actually goes.
        frag = re.search(r"#page=(\d+)", unquote(target))
        page_num = int(frag.group(1)) if frag else int(page)
        key = (name.strip().lower(), page_num)
        if key not in seen:
            seen.add(key)
            found.append((name.strip(), page_num))

    if not found:
        for name, page in BARE_RE.findall(answer):
            key = (name.strip().lower(), int(page))
            if key not in seen:
                seen.add(key)
                found.append((name.strip(), int(page)))
    return found


def looks_like_refusal(answer: str) -> bool:
    a = normalise(answer)
    return any(m in a for m in REFUSAL_MARKERS)


class Client:
    def __init__(self, base_url: str, token: str, workspace_id: int):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.workspace_id = workspace_id

    def upload(self, path: Path) -> Optional[int]:
        with path.open("rb") as fh:
            r = requests.post(
                f"{self.base}/api/v1/files",
                headers=self.headers,
                params={"workspace_id": self.workspace_id, "language": "English"},
                files={"files": (path.name, fh, "application/pdf")},
                timeout=300,
            )
        if r.status_code == 409:
            print(f"    already present: {path.name}")
            return None
        r.raise_for_status()
        items = r.json().get("files") or []
        return items[0]["id"] if items else None

    def list_files(self) -> List[Dict[str, Any]]:
        r = requests.get(
            f"{self.base}/api/v1/files",
            headers=self.headers,
            params={"page": 1, "page_size": 200, "workspace_id": self.workspace_id},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("items", [])

    def wait_until_processed(self, timeout_s: int = 1800) -> bool:
        """Ingestion is asynchronous, so asking too early scores a warm-up."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            files = self.list_files()
            states = {f["file_name"]: f.get("processing_status") for f in files}
            pending = {n: s for n, s in states.items() if s not in ("processed", "failed")}
            failed = [n for n, s in states.items() if s == "failed"]
            if failed:
                print(f"    FAILED to ingest: {failed}")
            if not pending:
                return not failed
            print(f"    waiting on {len(pending)}: {sorted(pending.values())}")
            time.sleep(15)
        print("    timed out waiting for ingestion")
        return False

    def ask(self, question: str) -> str:
        """One question, one fresh conversation, so nothing leaks between them."""
        r = requests.get(
            f"{self.base}/api/v1/messages",
            headers=self.headers,
            params={
                "message": question,
                "language": "english",
                "workspace_id": self.workspace_id,
            },
            timeout=300,
        )
        r.raise_for_status()
        body = r.json()
        if isinstance(body, str):
            return body
        for key in ("message", "answer", "content", "response"):
            if isinstance(body.get(key), str):
                return body[key]
        return json.dumps(body)


def score(q: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Judge one answer. Citations first, then content."""
    result: Dict[str, Any] = {
        "id": q["id"],
        "question": q["question"],
        "answer": answer,
        "cited": [{"file": f, "page": p} for f, p in parse_citations(answer)],
        "failures": [],
    }
    body = normalise(answer)

    if q.get("must_be_refusal"):
        refused = looks_like_refusal(answer)
        result["kind"] = "refusal"
        result["passed"] = refused
        if not refused:
            result["failures"].append(
                "answered a question the documents do not cover, instead of declining"
            )
        return result

    result["kind"] = "grounded"
    cited = parse_citations(answer)

    # Citation: every required source must be matched by at least one citation
    # landing on one of its acceptable pages. Extra citations are not failures;
    # answers legitimately draw on more than the minimum.
    missing_sources = []
    for c in q.get("citations", []):
        want_file = c["file"].strip().lower()
        want_pages = set(c["pages"])
        if not any(f.strip().lower() == want_file and p in want_pages for f, p in cited):
            missing_sources.append(f"{c['file']} p{sorted(want_pages)}")
    if missing_sources:
        result["failures"].append("did not cite: " + "; ".join(missing_sources))

    for term in q.get("must_include", []):
        if normalise(term) not in body:
            result["failures"].append(f"missing required text: {term!r}")

    for term in q.get("must_not_include", []):
        if normalise(term) in body:
            result["failures"].append(f"contains excluded text: {term!r}")

    result["passed"] = not result["failures"]
    result["cited_correctly"] = not missing_sources
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("BENCH_BASE_URL", "http://127.0.0.1:3000"))
    ap.add_argument("--token", default=os.getenv("BENCH_TOKEN"), help="Firebase ID token")
    ap.add_argument("--workspace", type=int, required=True)
    ap.add_argument("--corpus", type=Path, help="directory of PDFs to upload first")
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--label", default="run", help="name for this run, e.g. baseline")
    ap.add_argument("--only", type=int, nargs="*", help="run only these question ids")
    args = ap.parse_args()

    if not args.token:
        print("need --token or BENCH_TOKEN (a Firebase ID token)", file=sys.stderr)
        return 2

    spec = yaml.safe_load(args.spec.read_text())
    questions = spec["questions"]
    if args.only:
        questions = [q for q in questions if q["id"] in set(args.only)]

    client = Client(args.base_url, args.token, args.workspace)

    if args.corpus:
        print(f"uploading corpus from {args.corpus}")
        for pdf in sorted(args.corpus.glob("*.pdf")):
            print(f"  {pdf.name}")
            client.upload(pdf)
        print("waiting for ingestion")
        if not client.wait_until_processed():
            print("corpus is not fully ingested; scores would be meaningless")
            return 1

    print(f"\nasking {len(questions)} questions\n")
    results = []
    for q in questions:
        try:
            answer = client.ask(q["question"])
        except Exception as e:  # a transport failure is a failed question
            answer = f"__ERROR__ {e}"
        r = score(q, answer)
        results.append(r)
        mark = "pass" if r["passed"] else "FAIL"
        print(f"  {r['id']:>3}  {mark:4}  {q['question'][:52]:54}", end="")
        print("" if r["passed"] else f"  {r['failures'][0][:60]}")

    grounded = [r for r in results if r["kind"] == "grounded"]
    refusals = [r for r in results if r["kind"] == "refusal"]
    passed = [r for r in results if r["passed"]]
    cited_ok = [r for r in grounded if r.get("cited_correctly")]

    print(f"\n{'=' * 64}")
    print(f"  overall            {len(passed)}/{len(results)}")
    print(f"  citations correct  {len(cited_ok)}/{len(grounded)}   <- the one that matters")
    print(f"  refusals honoured  {len([r for r in refusals if r['passed']])}/{len(refusals)}")
    print(f"{'=' * 64}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-{args.label}.json"
    out.write_text(json.dumps({
        "label": args.label,
        "when": stamp,
        "base_url": args.base_url,
        "workspace": args.workspace,
        "totals": {
            "questions": len(results),
            "passed": len(passed),
            "grounded": len(grounded),
            "citations_correct": len(cited_ok),
            "refusals": len(refusals),
            "refusals_honoured": len([r for r in refusals if r["passed"]]),
        },
        "results": results,
    }, indent=2))
    print(f"\nwritten to {out}")
    print("diff against a previous run to see what a change actually did.")

    return 0 if len(passed) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
