# SyntextAI — Engineering Overview

Read this before touching architecture, design decisions, or planning what to
build next. Single source of truth for where the product actually stands,
not a full API reference. If you're tempted to write a new standalone doc
for a gap analysis, a feature list, or a product-direction question, put it
here instead, this file is where that kind of thing lives.

## What SyntextAI is

SyntextAI makes a company's internal documents — SOPs, handbooks, policy manuals —
instantly queryable by the whole team, with answers grounded in and cited back to the
actual source documents. Think "ask a question, get an answer with a citation," not a
generic chatbot.

**Target market:** SMBs, 10–50 employees, in document-heavy verticals — healthcare/dental,
accounting, legal, property management, trades & HVAC, insurance, and (added 2026-07-29)
manufacturing. One person's business (Osas), pre-revenue/early-revenue, moving fast.

**Manufacturing/maintenance-technician pivot considered and shelved (2026-07-29):** a
narrower reposition toward manufacturing plant maintenance technicians (50-500 employee
companies, $400-1,200/mo per facility) was drafted in detail — buyer persona, GTM,
product gaps, pricing — and briefly marked "ready to GTM." Osas's call: one prior
pilot (real, but a single data point) isn't enough validated demand to bet the whole
product's positioning on. Staying broad SMB. The underlying product still works for a
manufacturing user, RAG over any PDF is general-purpose, so "Manufacturing" was added
as a named vertical in the site's vertical selector (additive, not a repositioning).
If a real manufacturing customer materializes through that vertical tab, the shelved
gaps (table/diagram-aware PDF ingestion, symptom/fault-code query tuning, revision
control) are the ones to revisit, not before.

**Why it matters for design decisions:** this is a small team's product, not an
enterprise platform yet. Prefer straightforward, debuggable solutions over
infrastructure that only pays off at a scale we're not at. If you're choosing between
"simple and slightly less elegant" and "correct in theory but adds an operational
dependency," lean simple until there's a concrete reason not to.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, async SQLAlchemy, PostgreSQL, Alembic migrations |
| Frontend | React + TypeScript, Firebase Auth (client-side), Stripe, HashRouter |
| Storage | Google Cloud Storage (uploaded files) |
| LLM | DigitalOcean inference endpoint (OpenAI-compatible API) |
| Embeddings | Voyage AI |
| Email | SendGrid |
| Infra | DigitalOcean droplet, nginx reverse proxy (TLS termination), Docker Compose |
| Analytics | PostHog |

Local dev: `docker-compose -f docker-compose.yml -f docker-compose.local.yml up --build`,
or run the API directly with `uvicorn api.app:app --reload --host 0.0.0.0 --port 3000`
plus the worker in a separate process (`python -m api.workers.worker`). See
`env.example` for every config value the app expects — copy it to `.env` and fill in
values (your dev credentials come from Osas, not this repo).

## Codebase map

```
api/
  app.py            FastAPI app setup, middleware, router registration, startup/shutdown
  routes/           HTTP endpoints — one file per resource (files, messages, histories,
                     workspaces, users, subscriptions, analytics, internal)
  repositories/      All DB access lives here. Routes call repositories; repositories
                     own the SQLAlchemy queries. One repository per entity group,
                     orchestrated by repository_manager.py (the `store` object every
                     route depends on).
  models/           SQLAlchemy ORM models (orm_models.py) + DB session setup
  agents/           IngestionAgent, QueryAgent — the actual AI work (see "Async jobs"
                     below)
  rag/              Retrieval pipeline: HybridSearchEngine (semantic + keyword,
                     weighted), CrossEncoderReRanker, SmartChunkSelector, all wired
                     together via RAGFactory. Built behind interfaces (interfaces.py) —
                     swap an implementation without touching callers. This is the
                     entirety of "AI search" — there is no separate search surface,
                     it's the same engine chat uses (see "Known gaps").
  workers/          worker.py — polls the job queue and dispatches to agents
  workflows/        Internal job orchestration (tasks.py) — ingest/query dispatch, NOT
                     customer-facing workflow automation (see "Known gaps").
  processors/       File-type-specific ingestion: PDFProcessor (works), DocxProcessor
                     (works, added 2026-07-29), TextProcessor (built, not wired in).
                     Video/audio removed 2026-07-29.
  services/         External integrations — email, LLM calls, link processing
  core/             Cross-cutting: auth dependency, Firebase setup, rate/plan limits,
                     websocket manager, shared utils
  schemas/          Pydantic request/response models
  middleware/        (currently empty — see "Known gaps" below)
  alembic/          DB migrations
frontend/src/       React app
```

`docker-compose.yml` runs two containers off the same image: the API (`syntextaiapp`)
and the worker (`worker`), sharing a Postgres instance via `DATABASE_*` env vars.

## Data model

Core entities (see `api/models/orm_models.py`):

- **User** — one row per person, keyed to Firebase Auth via email lookup (auth itself
  is delegated to Firebase; the backend re-derives the user from the verified token on
  every request, it doesn't trust a client-supplied user ID).
- **Workspace / WorkspaceMember / WorkspaceInvite** — teams. A user belongs to one or
  more workspaces with a role (owner/staff). Invites are UUID tokens, 7-day expiry,
  single-use.
- **File / Segment / Chunk** — an uploaded document, broken into segments (logical
  sections) and chunks (retrieval units, target 300–500 tokens, structure-aware —
  chunk quality is most of what drives RAG answer quality here).
- **KeyConcept / Flashcard / QuizQuestion** — derived learning content from an earlier
  EdTech-flavored version of the product. Confirmed removed 2026-07-24: API routes
  deleted from `files.py`, frontend side panel stripped out of `FileViewerComponent.tsx`.
  The DB tables still exist (drop script written, not applied — see "Known gaps"), and
  the LLM-generation functions still exist as orphaned code in `workflows/tasks.py`.
- **ChatHistory / Message** — a conversation thread and its messages.
- **AgentRun** — the job queue (see below).
- **Subscription / CardDetails** — Stripe billing state.

Multi-tenancy is enforced at the **application layer**, not the database layer — there's
no Postgres RLS here (unlike a Supabase-style setup). Every query that touches
user- or workspace-scoped data must filter by the authenticated user's ID at the query
level. `histories.py` / `async_chat_repository.py` do this correctly (scope every query
by `user_id` in the `WHERE` clause). Some of the learning-content endpoints in
`files.py` didn't, until a recent fix — see "Known gaps" for what to watch for.

## How a request actually flows

**Chat query:**
1. Frontend sends `POST /api/v1/messages` with `history_id`, optional `workspace_id` /
   `file_id`, and the message text, with a Firebase ID token in the `Authorization`
   header.
2. Route re-derives the user from the token, verifies the caller owns `history_id` /
   `workspace_id` / `file_id`, saves the user message, and enqueues an `AgentRun` row
   (`run_type="answer_query"`) rather than answering inline.
3. The worker process (`worker.py`) polls `agent_runs` every 30s (`POLL_INTERVAL`),
   picks up queued rows (locked via `locked_by`/`locked_at` to avoid double-processing),
   and dispatches by `run_type` to the right agent.
4. `QueryAgent` runs retrieval (RAGFactory → HybridSearchEngine → reranker) and calls
   the LLM, writes the result back to `AgentRun.result`, and the frontend gets notified
   over the websocket connection (`websocket_manager.py`) rather than polling.

**File upload / ingestion:** same job-queue pattern — `save_file` in `files.py` uploads
to GCS, creates a `File` row, and enqueues `run_type="ingest_file"`, which
`IngestionAgent` picks up: extract → chunk → embed (Voyage AI) → store. Extraction is
delegated to `FileProcessingFactory` (`api/processors/factory.py`), which picks
`PDFProcessor` or `DocxProcessor` by file extension.

This decouples the request/response cycle from LLM latency — the API responds fast,
the actual AI work happens async in the worker. Worth understanding before changing
how any endpoint that touches files or chat behaves.

## What's promised vs. what's built

Osas Inc's marketing promises SyntextAI covers four capabilities. Checked against the
code, not assumed, last checked 2026-07-29:

| Promised | Reality |
|---|---|
| **Knowledge assistants** | Shipped, real. Chat with citations, the RAG pipeline described above, workspace/invite. No gap. |
| **AI search** | Not a separate capability. No standalone search route exists anywhere in `api/routes/`. It's the same `HybridSearchEngine` chat uses, described as two things in copy. Either stop marketing it as separate, or build an actual standalone search route + view on top of the existing (already-good) retrieval engine. |
| **Document processing** | PDF and DOCX work (`api/processors/factory.py`'s `processor_map`). `.txt`/`.md`/`.doc`/video/audio don't — `TextProcessor` is fully built at `api/processors/text_processor.py` but never imported by the factory, dead code. The site's file-type claims now match reality after the 2026-07-29 DOCX fix (see "Recent changes" below); check `Home.tsx`'s FAQ/pricing copy again before claiming `.txt`/`.md` support. |
| **Workflow automation** | Doesn't exist as a product feature. `api/workflows/tasks.py` is internal job orchestration (ingest/query dispatch), not customer-facing automation, no approval workflows, SOP generation, or business-process logic anywhere. This is Phase 3 on the roadmap below, don't market it before it's built. |

## Known gaps (read before you design around these)

**Security — came out of a full security pass, none of these are hypothetical:**
- **CORS is wide open** (`api/app.py`) — `allow_origins=["*"]` with
  `allow_credentials=True`. Needs to be locked to the actual frontend domain(s).
- **No rate limiting anywhere** — `api/middleware/` is empty. Chat and file upload
  trigger paid LLM/embedding calls with no per-user or per-IP throttle.
- **13+ endpoints return raw exception text to the client** (`detail=str(e)`) instead
  of a generic message with the real detail logged server-side. Follow the pattern in
  `histories.py` / the fixed parts of `messages.py`.
- **No security headers** (CSP, HSTS, X-Frame-Options) at either the app or nginx layer
  (`deploy.sh`) — only debug headers are currently set in production nginx config.
- **34 Dependabot vulnerabilities flagged on the repo** (1 critical, 14 high, 16
  moderate, 3 low) as of the 2026-07-29 push — not yet triaged. Check
  `github.com/Sarsue/SynTextAI/security/dependabot` before the next push.
- **No DB-level RLS** — every new route touching a resource ID from the URL/query needs
  an explicit ownership check (`check_ownership` helper exists, reuse it) or a
  `user_id`-scoped repository query. There's no safety net underneath you if you skip
  this, a broken-access-control bug in the flashcard/quiz endpoints shipped this way
  before it was caught and fixed.

**Product:**
- **DOCX ingestion closed 2026-07-29** — `DocxProcessor` implemented, mirrors
  `PDFProcessor`'s shape, wired into the factory. Extraction logic tested against a
  generated sample doc (headings, body, table); not yet tested through the real
  upload → GCS → worker → DB → chat-citation path end to end.
- **`TextProcessor` is built and orphaned** — exists, never wired into the factory's
  `processor_map` for `.txt`/`.md`. Confirm it actually works before wiring it in, it's
  never been exercised through the real pipeline.
- **EdTech generator functions are orphaned, not deleted** — `generate_mcq_from_key_concepts`
  and related functions in `workflows/tasks.py` are unreachable (the routes that called
  them were removed 2026-07-24) but the functions themselves weren't deleted. Harmless,
  cheap cleanup whenever that file is touched.
- **`docs/migrations/drop_learning_content_tables.sql` written, not applied** — drops
  the now-unused `flashcards`/`quiz_questions`/`key_concepts` tables. Needs a deliberate
  go-ahead from Osas before running against the real DB.
- **Alembic has multiple divergent heads and no tracked `alembic.ini`** — resolve this
  before writing the next real migration, don't guess a `down_revision` against an
  already-inconsistent chain.
- **Whether Redis (in `requirements.txt`, imported in `files.py`) is meant to be doing
  more than it currently is, or is leftover from an earlier design** — unconfirmed.

## Roadmap (prioritized, not a wishlist)

Update this section as items close or new ones turn up, don't spin up a new doc file
for it.

**Tier 0 — blocking, not features, fix before a real customer touches this:**
1. Alembic migration for `workspace_members`/`workspace_invites` not run on the live DB
2. Pending invite lost after login (`Auth.tsx` doesn't read `sessionStorage.pending_invite_token`)
3. Staff role enforcement missing on the backend (roles exist in DB, nothing stops staff uploading via direct API call)
4. Stripe end-to-end not confirmed on the live environment
5. CORS, rate limiting, exception leakage, security headers (see "Known gaps" above)
6. Triage the 34 Dependabot vulnerabilities

**Tier 1 — cheap, closes an existing gap:**
7. Wire `TextProcessor` into the factory for `.txt`/`.md` (confirm it works first)
8. Demo workspace — sample documents + example Q&A, needed for sales demos to work live
9. Clean up orphaned EdTech generator functions in `tasks.py`

**Tier 2 — Phase 2, stickiness (daily use, churn < 5%):**
10. Google Drive / SharePoint sync
11. Slack/Teams/WhatsApp bot — previously identified as the highest-impact SMB retention hook
12. Activity history
13. Answer feedback (thumbs up/down) — also generates real usage data for case studies, replacing illustrative ones
14. Admin dashboard with search analytics
15. Saved prompts

**Tier 3 — Phase 3, automation (search → action; this is where "workflow automation" becomes real, don't market it before it's here):**
16. AI-generated SOPs
17. Meeting summaries
18. Onboarding assistant
19. Proposal drafting
20. Approval workflows

**Tier 4 — Phase 4, AI operating layer (only plan for this once there are 50+ customers):**
21. CRM/email integrations, cross-chat memory, audit logs, API access

**Explicitly not being built right now:**
- Manufacturing-specific ingestion (table/diagram-aware PDF parsing, symptom/fault-code
  query tuning, revision control) — shelved with the manufacturing pivot, see above.
  Revisit only if a real manufacturing customer shows up through the vertical tab.
- Standalone AI search UI — revisit if a customer specifically asks for it.
- Voice interface — deferred, not in current scope.

Compliance is a stated differentiator (Canadian data residency option, no training on
customer data, SOC2 as a target, on-prem LLM option for regulated verticals) — keep
that in mind when weighing "fastest to ship" against "closer to what a
compliance-sensitive customer will eventually ask for," especially given the security
gaps above and the healthcare/legal verticals in the target market.

## Recent changes (chronological, most recent first)

- **2026-07-29:** DOCX processing implemented and closed. Manufacturing added as a
  named vertical (positioning stayed broad SMB, see "What SyntextAI is" above).
  Capability gap analysis performed against the four promised capabilities.
- **2026-07-29 (earlier):** YouTube/Whisper ingestion removed.
- **2026-07-24:** Manufacturing/maintenance-technician reposition drafted in detail,
  briefly marked "ready to GTM," then shelved. Flashcard/quiz/key-concept feature
  removal executed (routes + frontend panel; DB migration and `tasks.py` cleanup still
  pending).

## Questions to bring to Osas, not guess at

- Rate limiting approach/budget (per-user? per-workspace? which endpoints first?)
- Whether Redis is meant to be doing more than it currently is, or is leftover
- Priority/severity triage on the 34 Dependabot vulnerabilities
- Whether to build a standalone AI search surface or just fix the marketing copy
- When to run `docs/migrations/drop_learning_content_tables.sql` against the real DB
