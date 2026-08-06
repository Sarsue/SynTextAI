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
import unicodedata
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
    # Added after the first baseline: the pipeline declined the health-insurance
    # question correctly, in the words "doesn't give any details about", and was
    # scored as having answered it. The marker list was short, not the refusal.
    "does not give", "doesn't give", "do not give", "don't give",
    "no details", "does not detail", "not discuss",
    "does not include", "doesn't include", "do not include", "don't include",
    "does not offer", "doesn't offer", "not available in",
)


def normalise(text: str) -> str:
    """Whitespace- and punctuation-insensitive comparison text.

    PDFs are full of non-breaking spaces: the OSHA handbook writes
    "24\\xa0hours", so a naive substring check for "24 hours" fails on a page
    that plainly says it. The model will write an ordinary space, so compare on
    normalised text rather than making every question encode the PDF's quirks.

    Models reach for typographic punctuation the same way. The first baseline
    scored a correct answer wrong because it wrote "self\\u2011inspection" with a
    non-breaking hyphen, and read a citation as absent because the marker came
    back as "[Segment\\u202f1]" with a narrow no-break space. Fold the lookalikes
    to ASCII so the benchmark measures the answer and not the typography.
    """
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_LOOKALIKES)
    return re.sub(r"\s+", " ", t).lower()


# NFKC leaves these alone: they are distinct characters, not compatibility
# forms. U+2011 non-breaking hyphen, the dashes, the typographic quotes, and
# U+202F narrow no-break space (which NFKC maps to U+0020 only sometimes,
# depending on the Python build, so it is listed rather than assumed).
_LOOKALIKES = {ord(c): r for c, r in {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", " ": " ", " ": " ", " ": " ",
}.items()}


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
        self.last_history_id: Optional[int] = None

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
            # page_size is capped at 100 by the route; 200 is a 422, not a
            # bigger page.
            params={"page": 1, "page_size": 100, "workspace_id": self.workspace_id},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("items", [])

    def wait_until_processed(self, timeout_s: int = 1800) -> bool:
        """Ingestion is asynchronous, so asking too early scores a warm-up."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            files = self.list_files()
            # The route emits "status" while its declared FileResponse model
            # says "processing_status". Read both: the payload is the truth,
            # and reading only the documented name silently sees None.
            states = {
                f["file_name"]: f.get("status") or f.get("processing_status")
                for f in files
            }
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

    def new_history(self, title: str) -> int:
        r = requests.post(
            f"{self.base}/api/v1/histories",
            headers=self.headers,
            params={"title": title[:60], "workspace_id": self.workspace_id},
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        return body.get("id") or body.get("history_id") or body["history"]["id"]

    def ask(self, question: str, poll_timeout_s: int = 300) -> str:
        """Ask one question in its own conversation and wait for the answer.

        Answers are not returned by the request that asks. POST /messages
        enqueues an agent run and returns immediately; the answer is written
        into the conversation and pushed over a websocket. A runner that reads
        the POST response gets the question back and scores nothing.

        A fresh history per question keeps conversational context from leaking
        between them, which would make results depend on the order they ran in.
        """
        history_id = self.new_history(question)
        # Remembered so a trace can be tied back to the question that produced
        # it. Without this the traces exist and there is no way to know which
        # one belongs to "how long do i keep tax records".
        self.last_history_id = history_id
        before = len(self.messages(history_id))

        r = requests.post(
            f"{self.base}/api/v1/messages",
            headers=self.headers,
            params={
                "message": question,
                "language": "English",
                "history_id": history_id,
                "workspace_id": self.workspace_id,
            },
            timeout=120,
        )
        r.raise_for_status()

        deadline = time.time() + poll_timeout_s
        while time.time() < deadline:
            msgs = self.messages(history_id)
            replies = [
                m for m in msgs
                if (m.get("sender") or "").lower() not in ("user", "human")
                and (m.get("content") or "").strip()
            ]
            if replies and len(msgs) > before:
                return replies[-1]["content"]
            time.sleep(3)
        return "__TIMEOUT__ no answer within %ss" % poll_timeout_s

    def messages(self, history_id: int) -> List[Dict[str, Any]]:
        r = requests.get(
            f"{self.base}/api/v1/histories/messages",
            headers=self.headers,
            params={"history_id": history_id},
            timeout=60,
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else body.get("items", [])


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
        # A list is "any of these will do". The document says "five years" and
        # the model wrote "five (5) years", which is the same fact and was
        # scored as a miss; requiring one exact spelling of a number measures
        # phrasing, not grounding. Facts still have to appear, just not in one
        # blessed form.
        options = term if isinstance(term, list) else [term]
        if not any(normalise(o) in body for o in options):
            result["failures"].append(f"missing required text: {term!r}")

    for term in q.get("must_not_include", []):
        options = term if isinstance(term, list) else [term]
        hit = next((o for o in options if normalise(o) in body), None)
        if hit is not None:
            result["failures"].append(f"contains excluded text: {hit!r}")

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
    ap.add_argument(
        "--repeat", type=int, default=1,
        help=(
            "Ask everything this many times and report the spread. The model is "
            "not deterministic: two runs of identical code scored 16/21 and 14/21 "
            "on citations, with five questions flipping. Any comparison drawn from "
            "a single run of each side cannot see a change smaller than that."
        ),
    )
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

    passes = []  # per-run tallies, for the spread
    for run_index in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\n--- run {run_index} of {args.repeat} ---")

        # A Firebase ID token lasts an hour, and a --repeat 3 run outlives one.
        # Expiry used to surface as every question failing to cite, which reads
        # exactly like a catastrophic regression: a full three-run sweep scored
        # 0/26 and the cause was an expired token, not the pipeline. Ask one
        # cheap authenticated question first and stop if the answer is no.
        try:
            client.list_files()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            if code in (401, 403):
                print(
                    f"\nthe token is not valid ({code}). Mint a fresh one; scores from "
                    "an expired token are all zero and mean nothing.",
                    file=sys.stderr,
                )
                if not passes:
                    return 2
                # Runs already finished are still worth having. A --repeat 4
                # that lost its token on the fourth pass used to throw away
                # three complete runs, which is an hour of inference, because
                # the fourth could not start.
                print(
                    f"keeping the {len(passes)} run(s) that completed before the "
                    "token expired.",
                    file=sys.stderr,
                )
                break
            raise

        print(f"\nasking {len(questions)} questions\n")
        results = []
        for q in questions:
            try:
                answer = client.ask(q["question"])
            except Exception as e:  # a transport failure is a failed question
                answer = f"__ERROR__ {e}"
            r = score(q, answer)
            r["history_id"] = getattr(client, "last_history_id", None)
            results.append(r)
            mark = "pass" if r["passed"] else "FAIL"
            print(f"  {r['id']:>3}  {mark:4}  {q['question'][:52]:54}", end="")
            print("" if r["passed"] else f"  {r['failures'][0][:60]}")

        grounded = [r for r in results if r["kind"] == "grounded"]
        refusals = [r for r in results if r["kind"] == "refusal"]
        passed = [r for r in results if r["passed"]]
        cited_ok = [r for r in grounded if r.get("cited_correctly")]
        passes.append({
            "results": results,
            "passed": len(passed),
            "grounded": len(grounded),
            "citations_correct": len(cited_ok),
            "refusals": len(refusals),
            "refusals_honoured": len([r for r in refusals if r["passed"]]),
        })

        print(f"\n{'=' * 64}")
        print(f"  overall            {len(passed)}/{len(results)}")
        print(f"  citations correct  {len(cited_ok)}/{len(grounded)}   <- the one that matters")
        print(f"  refusals honoured  {len([r for r in refusals if r['passed']])}/{len(refusals)}")
        print(f"{'=' * 64}")

    if args.repeat > 1:
        cites = [p["citations_correct"] for p in passes]
        overall = [p["passed"] for p in passes]
        flipped = sorted({
            r["id"]
            for i in range(len(passes))
            for j in range(i + 1, len(passes))
            for r in passes[i]["results"]
            if r["passed"] != next(
                x["passed"] for x in passes[j]["results"] if x["id"] == r["id"]
            )
        })
        print(f"\n{'=' * 64}")
        print(f"  ACROSS {args.repeat} RUNS OF THE SAME CODE")
        print(f"  citations correct  {min(cites)}-{max(cites)} of {passes[0]['grounded']}"
              f"   (mean {sum(cites) / len(cites):.1f})")
        print(f"  overall            {min(overall)}-{max(overall)} of {len(passes[0]['results'])}")
        print(f"  unstable questions {flipped or 'none'}")
        print(f"\n  A change is only real if it moves the number by more than")
        print(f"  {max(cites) - min(cites)} citations, which is what this pipeline moves on its own.")
        print(f"{'=' * 64}")

    # Every run is written out, not just the last. With --repeat the useful
    # question is which questions fail *every* time and which merely flicker,
    # and only the first kind is worth chasing. Keeping one run threw away the
    # comparison the flag exists to make.
    all_runs = [p["results"] for p in passes]
    results = passes[-1]["results"]
    grounded = [r for r in results if r["kind"] == "grounded"]
    refusals = [r for r in results if r["kind"] == "refusal"]
    passed = [r for r in results if r["passed"]]
    cited_ok = [r for r in grounded if r.get("cited_correctly")]

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-{args.label}.json"
    out.write_text(json.dumps({
        "label": args.label,
        "when": stamp,
        "base_url": args.base_url,
        "workspace": args.workspace,
        "repeat": args.repeat,
        "spread": {
            "citations_correct": [p["citations_correct"] for p in passes],
            "passed": [p["passed"] for p in passes],
        },
        "totals": {
            "questions": len(results),
            "passed": len(passed),
            "grounded": len(grounded),
            "citations_correct": len(cited_ok),
            "refusals": len(refusals),
            "refusals_honoured": len([r for r in refusals if r["passed"]]),
        },
        "results": results,
        "runs": all_runs,
        # How often each question passed across the repeats. A question at 0/4
        # is a defect; one at 2/4 is the pipeline being unstable about it, and
        # the two need different work.
        "stability": {
            str(r["id"]): sum(
                1 for run in all_runs
                for x in run if x["id"] == r["id"] and x["passed"]
            )
            for r in results
        },
    }, indent=2))
    print(f"\nwritten to {out}")
    print("diff against a previous run to see what a change actually did.")

    return 0 if len(passed) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
