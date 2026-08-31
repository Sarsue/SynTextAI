"""Score what ingestion does to a PDF, before any model is involved.

    python api/evals/extraction_report.py /path/to/*.pdf

WHY THIS IS SEPARATE FROM run_benchmark.py

run_benchmark.py asks 22 questions through the live product and scores the
answers. It needs a running instance, a signed-in token and a workspace, and
every run costs model calls. It answers "are the answers good".

This answers "did we read the document correctly", which is a property of the
page and needs no model to check. It is free, it runs on any PDF in seconds,
and it is the right instrument when the question is whether the pipeline copes
with a new KIND of document rather than whether retrieval improved.

Use it when a customer sends a file that behaves oddly, and before trusting the
pipeline on a document type nobody has tried: a scanned contract, a tax form,
an invoice. Everything measured here has been wrong at least once.

WHAT IT REPORTS

    rows        A table row's first and last cell, still on one line. This is
                the property that was at 4% before 2026-08-30: a flash code
                table arrived as eleven codes followed by eleven meanings with
                nothing joining them, and 73 (contactor shorted) could not be
                told from 74 (contactor open).
    spaces      Multi-word cells whose words did not run together. The other
                extraction path returns "released inplace of the WavyFin",
                which the keyword arm of search cannot match.
    sections    Chunks that know which heading they sit under.
    junk        Chunks that contain no word at all. Page numbers, part codes.
    vision      Pages whose words are not in the file, so a model must read
                them. About 146 seconds each, so this column is usually the
                whole of a slow ingest.

WHY GROUND TRUTH RUNS IN A SUBPROCESS

Importing pymupdf4llm turns on Tesseract globally and changes what
page.find_tables() returns: cells come back with the spaces stripped, so
"Outdoor Air Temp Sensor" becomes "outdoorairtempsensor". Reference data
collected in the same process is silently corrupted, and the first version of
this measurement reported 2% against 11% for two extractors that were really at
4% and 78%. Both columns were comparing against nonsense.

So phase one runs in a clean interpreter that has never heard of pymupdf4llm,
writes JSON, and exits. Do not merge the phases back together.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List


# --------------------------------------------------------------------------
# Phase one: reference data, in a process with no pymupdf4llm in it
# --------------------------------------------------------------------------
def collect_truth(paths: List[str], out_path: str) -> None:
    import pymupdf

    truth: Dict[str, Any] = {}
    for path in paths:
        try:
            doc = pymupdf.open(path)
        except Exception as e:
            truth[path] = {"error": f"{type(e).__name__}: {e}"}
            continue
        pages: Dict[str, Any] = {}
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text() or ""
            try:
                tables = page.find_tables().tables
                rows = [r for t in tables for r in t.extract()]
            except Exception:
                rows = []
            try:
                drawings = len(page.get_drawings())
            except Exception:
                drawings = 0
            pages[str(i)] = {
                "rows": rows,
                "chars": len(text.strip()),
                "drawings": drawings,
            }
        truth[path] = {"page_count": doc.page_count, "pages": pages}
    with open(out_path, "w") as fh:
        json.dump(truth, fh)


# --------------------------------------------------------------------------
# Phase two: run the real pipeline and score it
# --------------------------------------------------------------------------
def _squash(s: Any) -> str:
    """Compare on characters. <br> is a cell-internal newline, and a lost space
    is a separate defect with its own column, not a lost row."""
    s = re.sub(r"<br\s*/?>", " ", str(s))
    return re.sub(r"\s+", "", s).lower()


def score(paths: List[str], truth: Dict[str, Any]) -> List[Dict[str, Any]]:
    import pymupdf
    import pymupdf4llm
    from api.core.utils import chunk_markdown
    from api.processors.pdf_processor import PDFProcessor

    processor = PDFProcessor(store=None)
    out: List[Dict[str, Any]] = []

    for path in paths:
        record = truth.get(path) or {}
        if record.get("error"):
            out.append({"name": os.path.basename(path), "error": record["error"]})
            continue

        started = time.time()
        try:
            doc = pymupdf.open(path)
            chunks_md = pymupdf4llm.to_markdown(doc, page_chunks=True)
        except Exception as e:
            out.append({"name": os.path.basename(path), "error": f"{type(e).__name__}: {e}"})
            continue
        elapsed = time.time() - started

        rows_ok = rows_total = 0
        sp_ok = sp_total = 0
        chunk_count = junk = with_section = 0
        table_pages = vision_pages = 0

        for i in range(doc.page_count):
            page_truth = (record.get("pages") or {}).get(str(i)) or {}
            rows = page_truth.get("rows") or []
            if rows:
                table_pages += 1

            md = ""
            if i < len(chunks_md):
                md = (chunks_md[i] or {}).get("text") or ""

            lines = [_squash(l) for l in md.splitlines() if l.strip()]
            body = re.sub(r"<br\s*/?>", " ", md).lower()

            for row in rows:
                filled = [c for c in row if c not in (None, "")]
                if len(filled) >= 2:
                    a, b = _squash(filled[0]), _squash(filled[-1])
                    if a and b and len(a) <= 150 and len(b) <= 150:
                        rows_total += 1
                        if any(a in l and b in l for l in lines):
                            rows_ok += 1
                for cell in row:
                    if cell in (None, ""):
                        continue
                    src = re.sub(r"\s+", " ", str(cell)).strip()
                    if " " in src and len(src) >= 12:
                        sp_total += 1
                        if src.lower() in body:
                            sp_ok += 1

            for chunk in chunk_markdown(md):
                chunk_count += 1
                content = chunk["content"]
                if len(re.sub(r"[^A-Za-z]", "", content)) < 3:
                    junk += 1
                if chunk["metadata"].get("section_path"):
                    with_section += 1

            # The real gate, not a copy of it, so this cannot drift from what
            # ingestion actually does.
            try:
                if processor._page_is_unreadable_without_vision(
                    doc[i], doc[i].get_text() or ""
                ):
                    vision_pages += 1
            except Exception:
                pass

        out.append({
            "name": os.path.basename(path),
            "pages": doc.page_count,
            "table_pages": table_pages,
            "rows": rows_total,
            "rows_pct": (100 * rows_ok // rows_total) if rows_total else None,
            "spaces_pct": (100 * sp_ok // sp_total) if sp_total else None,
            "chunks": chunk_count,
            "junk_pct": (100 * junk // chunk_count) if chunk_count else None,
            "section_pct": (100 * with_section // chunk_count) if chunk_count else None,
            "vision_pages": vision_pages,
            "vision_minutes": round(vision_pages * 146 / 60),
            "extract_seconds": round(elapsed, 1),
            "seconds_per_page": round(elapsed / max(doc.page_count, 1), 2),
        })
    return out


def _cell(value: Any, suffix: str = "") -> str:
    return "-" if value is None else f"{value}{suffix}"


def render(results: List[Dict[str, Any]]) -> None:
    print(
        f"{'document':<28}{'pg':>4}{'tbl':>5}{'rows':>7}{'spaces':>8}"
        f"{'sections':>10}{'junk':>6}{'vision':>8}{'time':>8}"
    )
    print("-" * 84)
    for r in results:
        if r.get("error"):
            print(f"{r['name'][:27]:<28}{'FAILED: ' + r['error'][:45]}")
            continue
        print(
            f"{r['name'][:27]:<28}{r['pages']:>4}{r['table_pages']:>5}"
            f"{_cell(r['rows_pct'], '%'):>7}{_cell(r['spaces_pct'], '%'):>8}"
            f"{_cell(r['section_pct'], '%'):>10}{_cell(r['junk_pct'], '%'):>6}"
            f"{str(r['vision_pages']) + 'p/' + str(r['vision_minutes']) + 'm':>8}"
            f"{str(r['seconds_per_page']) + 's/pg':>8}"
        )

    print()
    for r in results:
        if r.get("error"):
            continue
        # Said in words, because a column of numbers does not tell somebody
        # which of them is a problem.
        if r["rows_pct"] is not None and r["rows_pct"] < 60:
            print(f"  {r['name']}: table rows at {r['rows_pct']}%. Structure is being lost.")
        if r["spaces_pct"] is not None and r["spaces_pct"] < 60:
            print(f"  {r['name']}: words running together in {100 - r['spaces_pct']}% of cells.")
        if r["junk_pct"] is not None and r["junk_pct"] > 5:
            print(f"  {r['name']}: {r['junk_pct']}% of chunks contain no word at all.")
        if r["vision_minutes"] > 15:
            print(
                f"  {r['name']}: {r['vision_pages']} pages need the vision model, "
                f"about {r['vision_minutes']} minutes. Check they are really figures."
            )
        if r["seconds_per_page"] > 3:
            print(f"  {r['name']}: {r['seconds_per_page']}s a page to extract, which is slow.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="PDF files to score")
    ap.add_argument("--json", help="also write the results to this file")
    ap.add_argument("--_collect-truth", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._collect_truth:
        collect_truth(args.paths, args._collect_truth)
        return 0

    paths = [p for p in args.paths if os.path.exists(p)]
    for missing in set(args.paths) - set(paths):
        print(f"skipping {missing}: no such file", file=sys.stderr)
    if not paths:
        return 1

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        truth_path = fh.name
    try:
        # The subprocess is the point. See the module docstring.
        subprocess.run(
            [sys.executable, __file__, *paths, "--_collect-truth", truth_path],
            check=True,
        )
        with open(truth_path) as fh:
            truth = json.load(fh)
    finally:
        try:
            os.unlink(truth_path)
        except OSError:
            pass

    results = score(paths, truth)
    render(results)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
