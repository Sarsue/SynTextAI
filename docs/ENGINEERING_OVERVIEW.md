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

Local dev:

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev up --build -d
```

`docker-compose.local.yml` is standalone, not an override layer on
`docker-compose.yml`. It brings up three services: `postgres` (throwaway local
pgvector database on host port 5433), `syntextaiapp` (port 3000), and `worker`.

**Pass `--env-file .env.dev`.** It does two different jobs. The `env_file:` keys
inside the compose file already point the containers at `.env.dev`, but compose
interpolates `${...}` build args from `.env` by default, which is the production
file. Without the flag the frontend gets built with the **live** Stripe
publishable key, so test-mode checkout will not work.

| File | Used by | Stripe |
|---|---|---|
| `.env` | `docker-compose.yml`, deploys | `sk_live` / `pk_live` |
| `.env.dev` | `docker-compose.local.yml` | `sk_test` / `pk_test` |

Never hand-swap these. The local stack is bound to `.env.dev` so it cannot reach
live payment infrastructure.

Migrations run against the local database with:

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev run --rm --no-deps -w /app/api --entrypoint sh syntextaiapp -c "alembic upgrade head"
```

To reset to a clean database, `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`
then re-run the above. Rebuild the image (`--build`) after any `requirements.txt`
change; the `./api:/app/api` mount refreshes code but not installed packages.

Alternatively run the API directly with
`uvicorn api.app:app --reload --host 0.0.0.0 --port 3000` plus the worker in a
separate process (`python -m api.workers.worker`). See `env.example` for every
config value the app expects (your dev credentials come from Osas, not this repo).

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
                     (works, added 2026-07-29), TextProcessor (rewritten and wired in
                     for .txt/.md, 2026-07-29). Video/audio removed 2026-07-29.
  services/         External integrations — email, LLM calls, link processing
  core/             Cross-cutting: auth dependency, Firebase setup, rate/plan limits,
                     websocket manager, shared utils
  schemas/          Pydantic request/response models
  middleware/        (empty. The CORS, security-header and rate-limit middleware
                     added 2026-07-29 all live in app.py, not here. Delete this
                     package or move them into it; right now it misleads.)
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
| **Knowledge assistants** | Shipped, real, **and was silently broken until 2026-07-29** — see "Recent changes." Chat with citations, the RAG pipeline described above, workspace/invite. Fixed and live-verified against the real LLM endpoint now. |
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
- **Dependency vulnerabilities triaged 2026-07-29** (`gh` CLI wasn't available, ran
  `npm audit`/`pip-audit` directly against the actual lockfiles instead — counts won't
  match GitHub's Dependabot tally exactly, different scope/tooling, but same
  underlying packages):
  - **Frontend (`npm audit --omit=dev`): 40 findings originally, down to 30 after
    the firebase upgrade below.** `tar` (critical, no fix available upstream) comes
    in through `canvas` → `@mapbox/node-pre-gyp`, used only to extract prebuilt
    native binaries during `npm install`, never runs against user input at runtime,
    low practical risk despite the label. A second `undici` instance now comes from
    `shadcn` (a devDependency CLI tool) → `@dotenvx/dotenvx`, same low-risk
    reasoning: devDependency, doesn't ship to the browser bundle.
  - **`firebase` upgraded `^10.7.1` → `^12.16.0`, closing the original undici
    cluster (2026-07-29).** This app only imports `firebase/app` and
    `firebase/auth` (checked every import site), never firestore/storage/functions,
    which is where the vulnerable undici dependency chain actually lived — real
    risk surface was narrower than the audit output suggested. Verified before
    committing: `tsc --noEmit` against the whole app, zero errors; `npm run build`
    (the real production build, not just type-check), succeeds cleanly. Firebase
    12.x requires Node >=20; the actual Dockerfile already uses `node:20-alpine`,
    so production is unaffected, but anyone developing locally on Node 18 needs to
    upgrade. **Not yet tested: an actual live Google sign-in popup click-through**
    — types and build passing doesn't guarantee runtime auth behavior is identical.
    Osas is testing this live himself.
  - **Backend (`pip-audit -r api/requirements.txt`): 2 findings at the time, one now
    moot.** `dspy` 2.6.27 (`PYSEC-2026-1318`) — removed entirely 2026-07-29, see
    "Recent changes," it was never actually functioning. `diskcache` 5.6.3
    (`PYSEC-2026-2447`) isn't directly imported anywhere in this codebase, likely a
    transitive dependency of another package; no fix version available yet.
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
- **Whether Redis (in `requirements.txt`, imported in `files.py`) is meant to be doing
  more than it currently is, or is leftover from an earlier design** — unconfirmed.

## Roadmap (prioritized, not a wishlist)

Update this section as items close or new ones turn up, don't spin up a new doc file
for it.

**Tier 0 — blocking, not features, fix before a real customer touches this:**
1. ~~Alembic migration for `workspace_members`/`workspace_invites` not run on the live DB~~ — **verified already applied 2026-07-29.** `alembic current` matches the single head, and a direct `information_schema.tables` query confirmed both tables exist live. The "multiple divergent heads, no tracked alembic.ini" gap was also stale: only one head exists (`b8f6b3f63f7d`), and `alembic.ini` is git-tracked. `docs/migrations/drop_learning_content_tables.sql` was redundant for the same reason, the `flashcards`/`quiz_questions`/`key_concepts` drop it described had already happened via the alembic migration of the same name, confirmed those tables no longer exist, removed the now-dead script.

   **Correction, same day:** "applied on the live DB" was true but hid a real
   problem. Those tables were created *by hand*, and the migration that was
   supposed to create them was ordered before the one that creates `workspaces`,
   so building a database from scratch failed outright. Checking `alembic current`
   against a live database cannot catch this; only rebuilding from empty can.
   Fixed and verified with a full drift check, see "Recent changes."
2. ~~Pending invite lost after login~~ — **closed 2026-07-29**, see "Recent changes."
3. ~~Staff role enforcement missing on the backend~~ — **closed 2026-07-29**, see "Recent changes."
4. **Stripe reviewed 2026-07-29, real gap found: no 3D Secure / SCA handling.**
   `create_subscription` in `subscriptions.py` creates the subscription with a
   payment method directly and just returns whatever status Stripe gives back.
   If a card requires additional authentication (SCA, common on EU/UK cards,
   increasingly enforced elsewhere), Stripe returns status `"incomplete"`
   rather than `"active"`, and the subscription needs a client-side
   `stripe.confirmCardPayment(client_secret)` step to complete. Checked the
   frontend (`PaymentView.tsx`): no `confirmCardPayment`, no `client_secret`,
   no `requires_action` handling anywhere. `isCardUpdateRequired` (line ~292)
   treats any status outside `['none','active','deleted','canceled','trialing']`
   as "your card needs updating", which is the wrong message for a customer
   stuck in SCA, they'd be told to enter a new card when they actually just
   need to complete a 3D Secure popup. Net effect: some fraction of signups
   from SCA-required cards likely fail silently with a confusing message.
   Didn't attempt a blind fix, this needs live Stripe test-mode iteration
   (test-mode has cards that specifically trigger 3D Secure) to get right,
   not a guess. The rest of the trial → active → redirect flow (start-trial,
   the webhook handler's subscription.updated/deleted handling, Auth.tsx's
   status-based redirect) reads correctly on inspection; this SCA gap is the
   one substantive finding.
5. ~~CORS, rate limiting, exception leakage, security headers~~ — **closed 2026-07-29**, see "Recent changes."
6. ~~Triage the 34 Dependabot vulnerabilities~~ — **triaged 2026-07-29**, see "Known gaps" above.

**Tier 1 — cheap, closes an existing gap:**
7. ~~Wire `TextProcessor` into the factory for `.txt`/`.md`~~ — **closed 2026-07-29.** Rewrote it, the old version was incompatible with the current codebase, not just unverified.
8. ~~Demo workspace~~ — **not needed.** Osas already has a real test customer for demos instead of a synthetic one.
9. ~~Clean up orphaned EdTech generator functions in `tasks.py`~~ — **closed 2026-07-29.**

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

- **2026-07-29 (migration chain fixed, local Postgres added):** A fresh
  `alembic upgrade head` did not work at all. `add_workspace_members_invites`
  revised `fix_key_concepts_is_custom`, but both tables it creates carry a foreign
  key to `workspaces.id`, and `workspaces` is not created until `b2d181e6791f`,
  much later in the chain. Upgrading an empty database died on
  `UndefinedTable: relation "workspaces" does not exist`. Production never
  surfaced this because those two tables were created by hand, so the bad ordering
  was invisible for as long as nobody built a database from scratch. Fixed by
  repointing `down_revision` at `b2d181e6791f`, which keeps the existing merge at
  `befb0c5f5d70` valid because `20260227_teaching_agent` also descends from it.
  Does not replay in production: alembic stores only the current revision and prod
  is already at head.

  **Drift check result: clean.** All 30 migrations now apply to a fresh database
  reaching `b8f6b3f63f7d`, and the resulting schema matches live production
  exactly, 154 of 154 objects across columns, indexes, constraints and extensions.
  So production's hand-created tables happen to match what the migration produces,
  and prod needs no remediation. Worth re-running this check after any future
  hand-edit to the production schema; the comparison uses `information_schema` and
  `pg_indexes`/`pg_constraint` queries rather than `pg_dump`, so client/server
  version skew cannot introduce false differences.

  Added a `pgvector/pgvector:pg16` service to `docker-compose.local.yml` on host
  port 5433, with the app and worker gated on its healthcheck. Its credentials are
  **hardcoded, not interpolated**. Compose substitutes `${...}` from `.env`, which
  is the production file, so `${DATABASE_PASSWORD}` there would inject the real
  managed-database password into a local container. Local testing no longer touches
  production data.

  Also pinned the local stack to `.env.dev` in all four places (both services,
  both `env_file` and the `load_dotenv()` volume mount). Environment now follows
  the compose file instead of being hand-swapped. A branch cannot select an
  environment, because `.env*` files are gitignored and therefore identical no
  matter what is checked out; binding it in compose means the local stack always
  gets test-mode Stripe and can never reach live payment infrastructure.

  Note: the local image must be rebuilt after any `requirements.txt` change. The
  `./api:/app/api` mount refreshes code but not site-packages, so a stale image
  fails at import with `ModuleNotFoundError` for newer deps such as `slowapi`.

- **2026-07-29 (orphan code and package audit):** Removed `pdfminer.six` (imported
  in `pdf_processor.py` but never called; PyMuPDF does all extraction), ~12 unused
  Python imports across 8 files, 15 npm packages with no importer, and four dead
  files: `alembic/env_temp.py` (a debugging hack that mocked out pgvector),
  `models/db.py` (old sync session module superseded by `async_db.py`, zero
  importers), `routes/init.py` (empty), and `services/flashcard_quiz_utils.py`
  (EdTech leftovers whose backing tables no longer exist).

  Three packages that read as unused are **not**, and must stay. Now recorded inline
  in `requirements.txt`: `scikit-learn` (`cosine_similarity` imported lazily inside
  functions in `rag/search_engine.py` and `rag/reranker.py`), `nltk` (hard
  dependency of `llama-index-core`), `python-multipart` (required implicitly by
  FastAPI's `UploadFile` parsing). Likewise `tailwindcss`, `shadcn` and
  `tw-animate-css` on the frontend, which `depcheck` flags because they arrive via
  CSS `@import` in `src/index.css` rather than from TS. Lesson: neither `vulture`
  nor `depcheck` sees lazy imports or CSS imports, so verify every flagged package
  by hand before removing it.

  `alembic/versions/c9530c25560e_*.py` is an empty autogenerate stub (`pass`/`pass`).
  Left in place deliberately: `20260215_add_agent_runs` revises it, so deleting it
  would break the chain for no benefit.

- **2026-07-29 (multi-tenancy verified, invite gap fixed):** Checked whether the
  workspace model correctly handles a real scenario: someone invited as staff to
  workspace A later wants their own SyntextAI account and to invite others to their
  own workspace B. Verified sound at the schema and repository level:
  `count_workspaces_for_user` (used by the free-plan 1-workspace limit) only counts
  `Workspace.user_id == user_id`, i.e. *owned* workspaces, staff memberships in
  other people's workspaces never count against it. `list_workspaces_for_user`
  correctly merges owned + staff-membership workspaces into one list with the right
  role on each. `list_members` correctly synthesizes the owner (from
  `Workspace.user_id`) plus staff (`WorkspaceMember` rows) into one members list.
  No conflict, no double-counting, this works as expected.

  Found one real gap while checking this: `accept_invite` never verified the
  invite's target email matched the authenticated user's actual email, it just
  added whoever was logged in (with a valid, unexpired token) as staff. The token
  being an unguessable UUID4 was the only real protection, a forwarded email,
  pasted link, or leaked URL would let anyone with it join as staff regardless of
  who it was actually sent to. Fixed in `workspaces.py`'s `accept_invite` route:
  now compares the invite's email against the authenticated user's verified email
  (case-insensitive) before accepting, returns a clear 403 naming the expected
  email on mismatch.

- **2026-07-29 (critical fix):** Chat answer generation was silently broken in
  production. `generate_explanation_dspy` (called from `SyntextAgent.query_pipeline`,
  which is called from every live chat query via `QueryAgent._generate`) depended on
  a DSPy predictor (`explain_predictor`) that was never actually configured, the
  "configuration" code was a no-op `pass`. Every call fell through to a hardcoded
  placeholder string ("This section discusses X... (Explanation generated via
  fallback method)") that never contained a `[Segment N]` citation. `query_pipeline`
  requires at least one valid citation whenever context was retrieved, so **every
  real chat query with retrieved context was being refused** with "I couldn't find
  enough evidence in your documents to answer that confidently," regardless of what
  was actually in the uploaded documents. Fixed by renaming the function to
  `generate_explanation` and pointing it at `gradient_chat`, the already-working
  LLM-calling function used everywhere else in `llm_service.py` except this one path.
  Live-verified against the real DigitalOcean inference endpoint (not just compiled):
  a direct test call returned the exact expected output. Side finding from that same
  test: a 10-token `max_tokens` budget returned empty content from the model
  (`openai-gpt-oss-20b` appears to consume some budget on internal reasoning before
  output), 1500 worked reliably, worth keeping in mind for any other low-`max_tokens`
  call elsewhere in this file. This also made `dspy` genuinely removable, it was
  imported but never functionally exercised, removed from `requirements.txt` and the
  dead `explain_predictor`/`gemini_lm` scaffolding deleted.
- **2026-07-29 (Tier 0 sprint):** fixed the pending-invite-lost-after-login bug in
  `Auth.tsx`; added workspace-owner authorization to file upload (`files.py` had no
  membership check at all before this, any authenticated user could upload into any
  workspace_id); locked CORS to real domains; fixed 12 instances of raw exception
  leakage across 5 route files and, while doing that, found and fixed a real bug where
  4 Stripe subscription routes were re-catching their own legitimately-raised
  `HTTPException`s in a generic handler and mangling the status code/message; added
  security headers (CSP in report-only mode, HSTS, X-Frame-Options, etc.); added
  IP-based rate limiting to chat and upload; verified the `workspace_members`/
  `workspace_invites` migration was already applied (docs previously said otherwise),
  and that the "multiple divergent Alembic heads" gap was stale, only one head exists.
  DOCX processing implemented and closed. Manufacturing added as a named vertical
  (positioning stayed broad SMB, see "What SyntextAI is" above). Capability gap
  analysis performed against the four promised capabilities.
- **2026-07-29 (earlier):** YouTube/Whisper ingestion removed.
- **2026-07-24:** Manufacturing/maintenance-technician reposition drafted in detail,
  briefly marked "ready to GTM," then shelved. Flashcard/quiz/key-concept feature
  removal executed (routes + frontend panel; DB tables confirmed dropped and the
  `tasks.py` generator functions are the one remaining cleanup item).

## Questions to bring to Osas, not guess at

- Rate limiting budget (30/min chat, 10/min upload, IP-based) is a first-pass default,
  confirm or adjust once there's real usage data
- Whether Redis is meant to be doing more than it currently is, or is leftover
- Whether the firebase v12 upgrade's live Google sign-in flow actually works
  end to end (types and build pass; Osas is testing this live)
- Whether to build a standalone AI search surface or just fix the marketing copy
- Whether the report-only CSP policy is clean (check browser console for violations)
  before switching it to enforced
