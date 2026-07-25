# SyntextAI — Engineering Overview

Read this before touching architecture or design decisions. It's meant to get you from
zero to "I understand why this system looks the way it does" — not a full API reference.

## What SyntextAI is

SyntextAI makes a company's internal documents — SOPs, handbooks, policy manuals —
instantly queryable by the whole team, with answers grounded in and cited back to the
actual source documents. Think "ask a question, get an answer with a citation," not a
generic chatbot.

**Target market:** SMBs, 10–50 employees, in document-heavy verticals — dental clinics,
accounting firms, legal practices, home services, medical clinics. One person's business
(Osas), pre-revenue/early-revenue, moving fast.

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
                     swap an implementation without touching callers.
  workers/          worker.py — polls the job queue and dispatches to agents
  workflows/        Higher-level orchestration (tasks.py)
  processors/       File-type-specific ingestion (PDF, YouTube transcript, etc.)
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
- **KeyConcept / Flashcard / QuizQuestion** — derived learning content generated from
  a file. (Legacy from an earlier EdTech-flavored version of the product; the
  SMB-facing UI no longer surfaces flashcards/quizzes, but the backend and data model
  still support them — see "Known gaps.")
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
`IngestionAgent` picks up: extract → chunk → embed (Voyage AI) → store.

This decouples the request/response cycle from LLM latency — the API responds fast,
the actual AI work happens async in the worker. Worth understanding before changing
how any endpoint that touches files or chat behaves.

## Known gaps (read before you design around these)

- **CORS is wide open** (`api/app.py`) — `allow_origins=["*"]` with
  `allow_credentials=True`. Needs to be locked to the actual frontend domain(s) before
  this matters less than it currently does.
- **No rate limiting anywhere** — nothing in `requirements.txt`, `api/middleware/` is
  empty. Endpoints that trigger paid LLM/embedding calls (chat, file upload) have no
  per-user or per-IP throttle. If you're adding a new endpoint that calls an external
  paid API, this is the gap to close, not extend.
- **Broken access control was recently fixed but the pattern is easy to reintroduce** —
  flashcard/quiz-question/key-concept endpoints in `files.py` used to skip verifying
  that `file_id` belongs to the requesting user (a `check_ownership` helper now exists
  and is wired into every one of those routes — reuse it for anything new keyed off
  `file_id`). The lesson: because there's no DB-level RLS, every new route that takes
  a resource ID from the URL/query needs an explicit ownership check in the route or a
  `user_id`-scoped query in the repository. There's no safety net underneath you.
- **Flashcards/quizzes/key-concepts are vestigial** — full data model and endpoints
  exist, but the current SMB-facing product doesn't use them (an earlier EdTech
  pivot). Don't assume they're load-bearing; confirm with Osas before building on them
  or ripping them out.
- **13+ endpoints return raw exception text to the client** (`detail=str(e)`) instead
  of a generic message with the real detail logged server-side. Follow the pattern in
  `histories.py` / the fixed parts of `messages.py` (generic client message,
  `logger.error(..., exc_info=True)` for the real detail) for anything new.
- **No security headers** (CSP, HSTS, X-Frame-Options) at either the app or nginx layer
  (`deploy.sh`) — only debug headers are currently set in production nginx config.

None of these are hypothetical — they came out of a full security pass on this repo.
Worth reading as context for "why is X still open" rather than re-discovering them.

## Roadmap (so design choices don't paint us into a corner)

- **Phase 1 — Ship** (current): PDF/DOCX ingestion, chat with citations, workspace +
  invite, Stripe trial, onboarding. Goal: 5 paying customers.
- **Phase 2 — Stickiness**: Google Drive/SharePoint sync, Slack/Teams/WhatsApp bot,
  activity history, answer feedback, admin dashboard.
- **Phase 3 — Automation**: AI-generated SOPs, meeting summaries, approval workflows.
- **Phase 4 — AI Operating Layer**: CRM/email integrations, cross-chat memory, audit
  logs, API access. Only plan for this once there are 50+ customers — don't
  over-engineer for it now.

Compliance is a stated differentiator (Canadian data residency option, no training on
customer data, SOC2 as a target, on-prem LLM option for regulated verticals) — keep
that in mind if you're making a call between "fastest to ship" and "closer to what a
compliance-sensitive customer will eventually ask for."

## Questions to bring to Osas, not guess at

- Whether flashcards/quizzes/key-concepts get removed or revived
- Rate limiting approach/budget (per-user? per-workspace? which endpoints first?)
- Whether Redis (in `requirements.txt`, imported in `files.py`) is meant to be doing
  more than it currently is, or is leftover from an earlier design
