"""What a document contains, and on which page.

WHY

Retrieval can only find a page whose words happen to match the question. Asked
whether a shop must be wheelchair accessible, the pipeline cited page 8 of the
ADA guide, which explains accessible parking and uses the phrase "readily
achievable" in passing, rather than page 6, where that rule is defined. Both
pages contain the phrase. Only one answers the question.

A person would not have made that mistake, because a person opens the contents
page first. That is the capability neither the fixed pipeline nor the tool
agent had: no way to see what a document contains, only a way to guess words
and hope. Three separate retrieval levers were tried against that failure and
none of them closed it, because none of them changed what the model could do.

WHAT IT COSTS

Nothing at query time and almost nothing at ingestion. The extractor already
opens every PDF with fitz and reads its pages; asking the same open document
for its table of contents is one more call. Three of the five documents in the
benchmark corpus carry a real one:

    irs_publication_334   57pp   118 entries
    osha_handbook         98pp    64
    irs_publication_583   28pp    50
    ada_guide             20pp     0
    sba_planning_guide    24pp     0

The two without are handled by looking at font sizes, which is how a reader
recognises a heading and how the document itself encodes one.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MAX_ENTRIES = 400
# A heading has to stand out from the body, not merely differ from it. 1.15 was
# chosen by looking at the two documents here with no embedded contents: it
# keeps section titles and rejects the slightly-larger run-in text that some
# paragraphs start with.
HEADING_SIZE_RATIO = 1.15
MIN_HEADING_CHARS = 3
MAX_HEADING_CHARS = 120


def _from_toc(doc: Any) -> List[Dict[str, Any]]:
    """The document's own table of contents, when it has one."""
    try:
        toc = doc.get_toc() or []
    except Exception as e:
        logger.warning(f"Could not read embedded outline: {e}")
        return []

    out: List[Dict[str, Any]] = []
    for entry in toc[:MAX_ENTRIES]:
        try:
            level, title, page = entry[0], str(entry[1]).strip(), int(entry[2])
        except Exception:
            continue
        if title and page > 0:
            out.append({"level": int(level), "title": title[:MAX_HEADING_CHARS], "page": page})
    return out


def _from_font_sizes(doc: Any) -> List[Dict[str, Any]]:
    """Headings inferred from type size, for documents with no contents page.

    Two passes: find the body size first, because "large" only means anything
    relative to what the document mostly is. A cover page set in 24pt would
    otherwise define the threshold and every heading would fall under it.
    """
    sizes: Counter = Counter()
    spans_by_page: List[List[tuple]] = []

    for page in doc:
        page_spans: List[tuple] = []
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            spans_by_page.append(page_spans)
            continue
        for block in blocks:
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if not text:
                    continue
                size = max((s.get("size", 0) for s in line.get("spans", [])), default=0)
                rounded = round(size, 1)
                sizes[rounded] += len(text)
                page_spans.append((rounded, text))
        spans_by_page.append(page_spans)

    if not sizes:
        return []

    # The size that most of the document's *text* is set in, weighted by how
    # much text, so one large heading cannot outvote a page of body copy.
    body_size = sizes.most_common(1)[0][0]
    threshold = body_size * HEADING_SIZE_RATIO

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for page_index, page_spans in enumerate(spans_by_page, start=1):
        # Join consecutive lines set at the same size before judging them. A
        # heading that wraps arrives as two lines and would otherwise become
        # two entries; a pull quote set in large type arrives as eight and
        # would become eight. Merged, the heading stays short and the pull
        # quote grows past MAX_HEADING_CHARS and is dropped, which is the
        # distinction wanted and hard to draw any other way.
        merged: List[tuple] = []
        for size, text_ in page_spans:
            if merged and merged[-1][0] == size:
                merged[-1] = (size, f"{merged[-1][1]} {text_}")
            else:
                merged.append((size, text_))

        for size, text_ in merged:
            if size < threshold:
                continue
            text_ = text_.strip()
            if not (MIN_HEADING_CHARS <= len(text_) <= MAX_HEADING_CHARS):
                continue
            # A running header repeats on every page and is not a section.
            key = text_.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"level": 1, "title": text_[:MAX_HEADING_CHARS], "page": page_index})
            if len(out) >= MAX_ENTRIES:
                return out
    return out


def extract_pdf_outline(pdf_data: bytes) -> List[Dict[str, Any]]:
    """[{level, title, page}], in document order. Empty when nothing is found.

    Never raises: a document with no outline is merely harder to navigate, and
    failing the upload over it would be a poor trade.
    """
    try:
        import fitz  # PyMuPDF, already used by the PDF extractor
    except Exception as e:
        logger.warning(f"PyMuPDF unavailable, no outline: {e}")
        return []

    try:
        with fitz.open(stream=pdf_data, filetype="pdf") as doc:
            outline = _from_toc(doc)
            source = "embedded"
            if not outline:
                outline = _from_font_sizes(doc)
                source = "font sizes"
            if outline:
                logger.info(f"Outline: {len(outline)} entries from {source}")
            else:
                logger.info("Outline: none found")
            return outline
    except Exception as e:
        logger.warning(f"Outline extraction failed: {e}")
        return []


def render(outline: List[Dict[str, Any]], file_name: str) -> str:
    """The contents page as the model should read it."""
    if not outline:
        return f"{file_name} has no table of contents. Use search_documents instead."
    lines = [f"Contents of {file_name}:"]
    for entry in outline:
        indent = "  " * max(0, int(entry.get("level", 1)) - 1)
        lines.append(f"{indent}{entry.get('title')}  -> page {entry.get('page')}")
    return "\n".join(lines)
