"""Does asking for less make it faster, and does it stay correct?

Measured 2026-08-13: the vision endpoint's first token arrives in ~3s and it
then writes at roughly 3 tokens/second. So wall-clock time is very nearly a
linear function of how many tokens we ask for, and the current prompt asks it
to transcribe the whole page, including prose the text layer already has right.

Two prompts, same pages, interleaved and repeated, because a single run of each
cannot tell a real difference from endpoint noise.

Correctness is checked against values read off the PDFs by hand on 2026-08-12:
  page  9  charging chart:  74, 94, 108
  page 62  physical data:   4350 (size 48 CFM), 670 (high pressure open), 1/12
A faster prompt that loses any of these is not faster, it is broken.
"""
import asyncio
import base64
import json
import time

import fitz
import httpx

from api.services.llm_service import (
    MODEL_ACCESS_KEY,
    INFERENCE_BASE_URL,
    VISION_MODEL,
    VISION_DPI,
    VISION_PROMPT,
    VISION_MAX_TOKENS,
    VISION_DEADLINE,
)

doc = fitz.open("/tmp/carrier.pdf")

# The prompt in production today.
FULL = VISION_PROMPT

# Only the parts the text layer destroys. Prose survives extraction intact, so
# paying 3 tokens/second to have it retyped buys nothing and risks a model
# rewriting text that was already correct.
TABLES_ONLY = (
    "This page comes from a technical manual. Its plain paragraphs have already "
    "been extracted correctly, so do not transcribe them.\n\n"
    "Transcribe ONLY the tables, charts and labelled diagrams on this page:\n"
    "- every table as a real markdown table, all rows and columns in their "
    "original order, nothing summarised, nothing omitted, nothing corrected\n"
    "- every labelled diagram as a short description followed by each label "
    "kept with the part it points to\n\n"
    "If the page has no table, chart or labelled diagram, reply with exactly: NONE"
)

EXPECT = {
    9: ["74", "94", "108"],
    62: ["4350", "670", "1/12"],
}


async def run(page_no: int, prompt: str, label: str):
    png = doc[page_no - 1].get_pixmap(dpi=VISION_DPI).tobytes("png")
    body = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        ]}],
        "max_tokens": VISION_MAX_TOKENS,
        "temperature": 0,
        "stream": True,
    }
    t0 = time.monotonic()
    ttft = None
    out = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, read=900)) as c:
        async with c.stream(
            "POST", f"{INFERENCE_BASE_URL.rstrip('/')}/chat/completions",
            json=body, headers={"Authorization": f"Bearer {MODEL_ACCESS_KEY}"},
        ) as r:
            if r.status_code != 200:
                await r.aread()
                print(f"{label:12s} p{page_no}: HTTP {r.status_code}", flush=True)
                return
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    d = json.loads(chunk)
                except Exception:
                    continue
                tok = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
                if tok:
                    if ttft is None:
                        ttft = time.monotonic() - t0
                    out.append(tok)
                    # Progress, so a runaway is visible in seconds rather than
                    # after half an hour of silence.
                    if len(out) % 150 == 0:
                        print(f"    .. {label} p{page_no}: {len(out)} tok, "
                              f"{time.monotonic()-t0:.0f}s", flush=True)
                elapsed = time.monotonic() - t0
                if elapsed > VISION_DEADLINE:
                    print(f"{label:12s} p{page_no}: DEADLINE at {elapsed:.0f}s "
                          f"after {len(out)} tokens", flush=True)
                    return
    total = time.monotonic() - t0
    md = "".join(out)
    missing = [v for v in EXPECT[page_no] if v not in md]
    verdict = "OK" if not missing else f"LOST {missing}"
    print(f"{label:12s} p{page_no}: {total:5.0f}s  ttft={ttft or 0:4.1f}s  "
          f"out={len(md):5d} chars  {verdict}", flush=True)


async def main():
    for _ in range(2):
        for page_no in (9, 62):
            await run(page_no, FULL, "full")
            await asyncio.sleep(10)
            await run(page_no, TABLES_ONLY, "tables-only")
            await asyncio.sleep(10)


asyncio.run(main())
