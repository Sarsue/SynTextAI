# SyntextAI — Capability Gap Analysis (GTM Readiness)

Living doc, not a one-time snapshot. Update it whenever a gap closes or a new
one turns up. Companion to `ENGINEERING_OVERVIEW.md` (architecture) and
`PRD_KNOWLEDGE_ASSISTANT.md` (product scope).

## Why this doc exists

Osas Inc's marketing copy promises SyntextAI covers four capabilities:
**knowledge assistants, AI search, document processing, and workflow
automation.** This doc checks that promise against the actual code, not
against what the roadmap says or what sounds right. Sell what's built; know
exactly what isn't before a demo makes a claim the product can't back up.

## The four capabilities, checked against the code

### 1. Knowledge assistants — shipped, real

Chat with citations, backed by `RAGFactory` → `HybridSearchEngine` (semantic
+ keyword) → `CrossEncoderReRanker`. Workspace/team invite works. This is
the actual product and it's solid. No gap here.

### 2. AI search — not a separate capability, it's the same thing as #1

There is no standalone search endpoint anywhere in `api/routes/` (checked
every route file — zero `/search`-style routes). The retrieval engine is
real, but it's only reachable through the chat pipeline. If a prospect
expects "search" as its own feature (type a query, get a ranked list of
passages, no conversation), that doesn't exist. Either stop describing it
as a separate capability in marketing copy, or build a standalone search UI
on top of the existing `HybridSearchEngine` (the hard part, retrieval
quality, is already done; this would mostly be a new route + frontend
view).

### 3. Document processing — PDF only, and there's a live false claim

`api/processors/factory.py`'s `processor_map` (lines 49-72):

| Extension | Processor | Status |
|---|---|---|
| `pdf` | `PDFProcessor` | Works |
| `docx`, `doc` | `None` | **Not implemented** — `# TODO: Implement DocxProcessor` |
| `txt`, `md` | `None` | **Not implemented** — `TextProcessor` class exists at `api/processors/text_processor.py` but is never imported by the factory or anywhere else. Dead code, not wired in. |
| `mp4`/`mov`/`avi`/`mkv`/`webm`, `mp3`/`wav`/`m4a` | `None` | Not implemented (video/audio ingestion was recently and deliberately removed — see git log `777d863`) |

**Live false claim:** `frontend/src/Home.tsx` says "PDF and Word (.docx)
files" are supported in four places (the FAQ answer, the "How it works"
step 1 copy, and both pricing tier feature lists — lines 98, 180, 275, 292).
A .docx upload today has no processor and will fail. This isn't a roadmap
gap, it's a live promise the product can't keep right now.

Separately, for manual/technical-manual-heavy customers specifically:
`PDFProcessor` OCRs image-only pages to plain text with no table-structure
or diagram-aware handling — torque spec tables, exploded parts diagrams,
wiring schematics lose their structure. Only matters if pursuing that kind
of document; SOPs/policy manuals (the current core use case) are fine with
plain-text extraction.

### 4. Workflow automation — doesn't exist as a product feature

`api/workflows/tasks.py` is internal job orchestration (dispatches ingest
and query jobs from the `agent_runs` queue) — not customer-facing
automation. No approval workflows, SOP generation, meeting summaries, or
any business-process automation logic exists anywhere in the codebase.
`ENGINEERING_OVERVIEW.md`'s own roadmap places this at Phase 3 (5-8
months out) — it is explicitly not current scope, not an oversight.

Also found while checking this: `tasks.py` still contains
`generate_mcq_from_key_concepts`, `generate_flashcards_from_key_concepts`,
`generate_true_false_from_key_concepts`, and related functions from the
old EdTech pivot. The PRD's 2026-07-24 removal only deleted the API routes
in `files.py` — these generator functions are orphaned (unreachable, no
route calls them) but were never deleted. Harmless, but worth a cleanup
pass along with the `text_processor.py` dead code.

## Urgent — fix before the next real customer conversation

1. **DOCX claim is false and live in four places** (`Home.tsx` lines 98,
   180, 275, 292). Fastest fix: remove the claim, say "PDF" only until a
   `DocxProcessor` exists. Real fix: build `DocxProcessor` following the
   `FileProcessor` interface `PDFProcessor` already implements.
2. **`text_processor.py` is fully built and orphaned** — wiring it into
   `factory.py`'s `processor_map` for `txt`/`md` is probably a small,
   fast win to close (confirm it actually works end-to-end first, it's
   never been exercised via the factory path).

## Not urgent, but worth knowing before a demo

- "AI search" in any pitch should be described as *how* the knowledge
  assistant works, not a separate thing a prospect gets.
- "Workflow automation" should not appear in any SyntextAI-specific pitch
  right now — it's an Osas Inc Implementation (custom-build) capability,
  not something SyntextAI-the-SaaS-product does today.
- Dead code cleanup (`text_processor.py` if not wired in, orphaned EdTech
  generators in `tasks.py`) is low priority but cheap to do in one pass.

## Tie-in to the roadmap

See `ENGINEERING_OVERVIEW.md` → Roadmap. Phase 1 (current) is PDF
ingestion + chat with citations + workspace/invite + Stripe trial, exactly
capability #1 above. Phases 2-4 are where AI search (maybe), workflow
automation, and broader document support actually show up, not now.
