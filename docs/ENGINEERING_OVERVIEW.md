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

**Target market:** SMBs, 10–100 employees, in document-heavy verticals — healthcare/dental,
accounting, legal, property management, trades & HVAC, insurance, and (added 2026-07-29)
manufacturing. One person's business (Osas), pre-revenue/early-revenue, moving fast.

**Positioning (restated 2026-08-01): AI for small and medium businesses, meeting
them in the tools they already use.** Documents are where this starts, not where
it stops. A 10–100 person company keeps what it knows in a handful of systems,
and the ones that matter differ by vertical: a dental practice's practice
management system, a firm's document management system, the shared drive
everyone actually saves to. The direction is integrations into those systems for
the domains we choose to serve, so the answer is grounded in what the business
already has rather than in what somebody remembered to upload.

Two consequences for design decisions. Ingestion should stay indifferent to where
bytes came from, because a connector is another source of documents and not a
second product. And the tenant boundary has to hold for real, because a connector
imports a company's whole drive, and the blast radius of getting access wrong
stops being one uploaded file.

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
  rag/              Two modules: SmartChunkSelector (fits passages to a token
                     budget) and DefaultQueryProcessor (rewrites and expands a
                     question, and splits it into information needs).
                     HybridSearchEngine, CrossEncoderReRanker, RAGFactory and
                     interfaces.py were deleted on 2026-08-05. The reranker
                     re-embedded content[:600] with the same bi-encoder that
                     produced the score it claimed to improve, and was deleting
                     correct pages; the rest was dependency-injection scaffolding
                     for two concrete classes with no alternates. Retrieval now
                     lives in SQL, in async_file_repository.hybrid_search.
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

Rewritten 2026-08-07. The previous version described a RAGFactory, a
HybridSearchEngine and a reranker, none of which exist any more. Read this one
against the code before trusting it; a stale flow diagram is worse than none,
because it is believed.

### Ingesting a document, once

```
upload -> GCS
       -> fitz: text per page, plus the table of contents -> files.outline
       -> chunk_text: 400 tokens, 20% overlap
       -> embed each chunk (Voyage AI, voyage-3.5-lite, 1024-dim)
       -> ONE segment per page      the citation unit, a page is what a reader opens
          N chunks beneath it       the retrieval unit, what gets embedded and searched
       -> zero chunks means the file is marked FAILED, not processed
```

`chunks.segment_id` is what ties a retrieved chunk back to the page it is cited
as. Documents ingested before 2026-08-07 have a null `chunks.content` and
retrieval falls back to the segment's text for them, so they behave as
page-sized until re-uploaded.

### Asking a question

```
POST /api/v1/messages          the question is in the BODY, never the URL
      |
      v
route authorizes, saves the user message, enqueues an AgentRun, returns 201
      |
      v
worker polls agent_runs, dispatches by run_type, runs QueryAgent
```

The API answers immediately and the model work happens in the worker, so nothing
is blocked on inference. The browser is told over the websocket rather than
polling.

### The graph

```
process_query -> retrieve -> dedupe_and_normalize -> check_coverage
                    ^                                     |
                    +-------- gap, under the cap ---------+
                                                          v
                                       select_context -> generate -> END
```

**retrieve** is one SQL statement holding two indexed searches, fused:

```sql
vec    top 100 by  embedding <=> query      -- HNSW, cosine
txt    top 100 by  ts_rank_cd(tsv, query)   -- GIN, english
fused  SUM(weight / (60 + rank))            -- reciprocal rank, 0.7 vector / 0.3 keyword
       -> top 25
```

Ranks are fused rather than scores because a cosine similarity and a ts_rank_cd
share no scale, and the weighted blend of them that this replaced was arithmetic
on incomparable units.

**check_coverage** decides by retrieval, not by judgement: a need counts as
covered when a search aimed at it contributed passages the set did not already
hold. No model is asked. With `MAX_RETRIEVALS` at 1 the loop never fires and this
is a straight line; see the entry on retrieval per information need for when
raising it is worth it.

**generate** is a single model call. The grounding contract comes first and the
citation mechanics after, and the model emits `[Segment N]` rather than writing
a file name, because an index is a token it cannot get wrong.

### On the way out

```
citations validated   a citation to a segment the model was not shown is dropped
links filtered        only hosts we serve documents from stay clickable
stored, websocket     the browser is notified
```

### The shape of it

One path. Retrieval happens in SQL rather than in Python. The model has one job,
which is to read passages and write an answer citing them, and every claim it
makes about that is checked in code afterwards: which segments it cited, and
where it is allowed to link. The loop exists in the graph and is capped, so today
it is a line rather than a cycle.

Settings in force:

```
openai-gpt-oss-20b   temperature 0.1   window 120k
MAX_RETRIEVALS 1     RETRIEVAL_TOP_K 25   candidate pool 100   weights 0.7/0.3
```

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

**Security — came out of a full security pass. The first four are now closed;
verified 2026-08-01, listed because knowing they were once open is what stops
them being reopened:**
- ~~CORS wide open~~ **Closed.** `api/app.py` reads `CORS_ORIGINS`, falling back
  to the real domains plus local dev ports. Never widen this back to `*` while
  `allow_credentials=True`; that combination lets any site on the internet make
  authenticated requests with a logged-in user's credentials.
- ~~No rate limiting~~ **Closed.** `api/core/rate_limit.py` holds a shared
  limiter; endpoints opt in, and the paid-API paths (chat, upload) are covered.
- ~~Raw exception text returned to clients~~ **Closed.** No `detail=str(e)`
  remains in `api/routes/`. Routes re-raise `HTTPException` before the broad
  handler, so a 403 or 404 stays a refusal instead of being turned into a 500 that
  reads as the app being broken.
- ~~No security headers~~ **Closed.** CSP (Report-Only), HSTS, X-Frame-Options,
  X-Content-Type-Options and Referrer-Policy are set in `api/app.py`. The CSP is
  still Report-Only: check the browser console for violations, then flip the
  header name. `frame-src` must keep `storage.googleapis.com` or every citation
  breaks when it is enforced.
- **Still open: no DB-level RLS.** See below.
- **Re-triaged 2026-08-03: down to 5 from 34, and the remaining ones have
  nowhere to go.** The backend is clean, `pip-audit` finds nothing. The frontend
  has two highs and neither has a fix that is worth taking:
  - **react-router / react-router-dom**, GHSA-qwww-vcr4-c8h2, "RSC Mode CSRF
    Bypass". The advisory covers `7.12.0 - 8.2.0` and **no patched version has
    been published in any line** — npm's suggested "fix" is a downgrade to
    6.30.4, a major-version break across the whole routing layer. The vulnerable
    surface is React Server Components mode, and this app imports only
    `HashRouter`, `Routes`, `Route`, `Navigate`, `Link`, `useNavigate`,
    `useLocation`, `useSearchParams`, `useParams`. No server handler, no data
    router, no actions or loaders. Not reachable. Recheck when a patched 7.x or
    8.x appears; do not downgrade.
  - **vite**, `server.fs.deny` bypass **on Windows**, in the dev server. A
    devDependency that never ships, an OS nobody here develops on, and the fix
    is vite 8 from 5.4.21 — three majors of build-tool risk for a hole that
    cannot be reached in this setup. Worth taking when the build tooling is
    upgraded deliberately, not as a security response.

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
- **No DB-level RLS** — every new route touching a resource ID from the URL or query
  needs an explicit check. There is no safety net underneath you if you skip it; a
  broken-access-control bug in the flashcard/quiz endpoints shipped this way before
  it was caught and fixed.

  Since 2026-08-01 there are exactly two questions and two ways to ask them.
  *May you see it* is `accessible_workspace_ids`. *May you do it* is
  `assert_workspace_capability` / `assert_organization_capability` from
  `api/core/permissions.py`. Do not answer either from `files.user_id`, from
  `list_workspaces_for_user`, or from a role string compared inline. Every
  authorization bug in this codebase so far has been one of those three.

**Product:**
- **DOCX ingestion closed 2026-07-29** — `DocxProcessor` implemented, mirrors
  `PDFProcessor`'s shape, wired into the factory. Extraction logic tested against a
  generated sample doc (headings, body, table); not yet tested through the real
  upload → GCS → worker → DB → chat-citation path end to end.
- ~~`TextProcessor` built and orphaned~~ **Closed.** Wired into
  `factory.py`'s `processor_map` for `.txt` and `.md`.
- ~~EdTech generator functions orphaned~~ **Closed.** Deleted.
- **Whether Redis (in `requirements.txt`, imported in `files.py`) is meant to be doing
  more than it currently is, or is leftover from an earlier design** — unconfirmed.
  Only `RedisError` is imported, so today it is doing nothing.
- **Integrations are not started.** The positioning above commits to meeting
  businesses in the tools they use, and nothing in the codebase reads from an
  external system yet. When it does, it should arrive as another source feeding the
  same ingestion path, not as a parallel pipeline, and a connector's imported
  documents belong to a workspace like any other.

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
4. ~~Stripe: no 3D Secure / SCA handling~~ — **closed 2026-08-07**, built and
   verified against Stripe test mode, see "Recent changes". The description
   below is the original finding, kept because it is still the clearest
   statement of what was wrong. Two things in it have since been corrected by
   the build: the secret is on `latest_invoice.confirmation_secret`, not
   `latest_invoice.payment_intent`, which is null on this API version; and
   `isCardUpdateRequired` now lists the statuses it means instead of excluding
   the ones it doesn't.

   **Stripe reviewed 2026-07-29, real gap found: no 3D Secure / SCA handling.**
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

**Tier 1 — cheap, closes an existing gap. Closed 2026-08-03.** All three items
below are done. The efficiency backlog that follows is a separate list and is
*not* closed: items 18-22 remain genuinely open.

7. ~~Wire `TextProcessor` into the factory for `.txt`/`.md`~~ — **closed 2026-07-29.** Rewrote it, the old version was incompatible with the current codebase, not just unverified.
8. ~~Demo workspace~~ — **not needed.** Osas already has a real test customer for demos instead of a synthetic one.
9. ~~Clean up orphaned EdTech generator functions in `tasks.py`~~ — **closed 2026-07-29.**

**Efficiency backlog (audited 2026-07-29 against a longer external review):**

Closed, or moot after the EdTech and YouTube removals:

| Item | Status |
|---|---|
| Prefer captions over Whisper; keep model warm | Moot, `faster-whisper`/`yt-dlp` gone |
| Defer flashcards/quizzes until chat-ready | Moot, that stage no longer exists, so `processed` **is** chat-ready |
| Heavy models loaded in API contexts | Moot, nothing heavy left to load |
| Many sequential LLM calls for learning materials | Moot, same reason |
| Per-stage timing metrics | **Done 2026-07-29**, see "Recent changes" |
| Size/type-based worker concurrency | **Done 2026-07-29**, plus the batch barrier and lease bugs found alongside it |

Premise checked and found wrong, so **do not act on these**:

- *"Q&A sends a ~120k-token prompt."* It does not. `rag/pipeline.py` selects context at `token_budget=3000`, already inside the recommended 3k–8k. `MAX_TOKENS_CONTEXT=120000` is only a ceiling that triggers *reduction*. The one real sliver: chat history is not capped separately from document context.
- *"Frontend duplicates status work via polling and WebSocket."* There is no `setInterval` anywhere in the frontend. WebSocket is the only continuous channel; `pollFileStatus` is a one-shot manual nudge that already skips terminal statuses.

Genuinely still open, roughly in value order:

18. Large-PDF partial usability: no persisted progress (a crash restarts from page 1) and no fast path to unlock chat on the first N pages. Batching and empty-chunk filtering already exist.
19. Event queue instead of polling. `POLL_INTERVAL` defaults to 30s, so that is the idle-latency floor before work starts. `redis>=5.0.0` is already a dependency, so this is cheaper than it looks.
20. Embedding dedupe by chunk hash, so re-uploads and retries stop re-paying the embedding cost.
21. Short-TTL query cache keyed by user, document set, and normalized question.
22. Redis pub/sub for worker-to-API notifications instead of the current sync `requests.post` wrapped in `to_thread`.

Success metrics to watch once the TIMING logs have data: p50/p95 upload to chat-ready, p50/p95 query latency, queue wait, worker RSS peaks, and failed/retried runs per stage.

**Tier 2 — Phase 2, stickiness (daily use, churn < 5%):**

**1. The agentic platform. The Tier 2 initiative (direction set 2026-08-03).**

Tier 2 is one thing: an agent that uses tools and skills, running open models we
serve ourselves, with the DigitalOcean endpoint gone. Not a cost optimisation
that happens to involve models. The product stops being "ask a question about
your documents, get a cited answer" and becomes "give it something to do".

This was previously written up as a self-hosted model platform gated on hosted
inference passing $500-1,000/month. That framing was wrong about the reason.
Waiting for a cost threshold made sense for a migration whose only payoff was a
cheaper bill; it makes no sense for the thing the product is meant to become.
The phases below survive intact, because they were always the right sequence.
Only the trigger changed: it is a decision now, not a threshold.

**What agentic means here, concretely.** A tool is something the model can call:
search these documents, read this page, list the workspaces, draft this. A skill
is a named procedure over tools that a customer recognises as their own work,
which is where the vertical focus pays: "check this invoice against the contract"
for accounting, "find every clause about termination" for legal. Orchestration is
the model deciding which to use and in what order, then composing a cited answer.

**Status, measured 2026-08-04: the tool layer is built and it is losing.**
`AGENT_MODE=tools` exists, works, and scores **13-14/22** on citations against
the fixed pipeline's **16-18/22**, on the same corpus with two runs each. It
defaults to off. Two things it does are genuinely better and neither is a
number the pipeline can reach by tuning: refusals hit 4/4, and a question
spanning two documents cites both, because the model searched twice.

Read that as a schedule, not a verdict. The pipeline has had a day of defect
fixes behind it and the tool loop has had one. But the ordering stands: **the
tool layer only replaces the pipeline when it beats it on citations**, and
"the architecture is more interesting" is not a reason to ship it. The
benchmark decides, and it now knows its own noise, so the decision is
checkable rather than argued.

**The job queue is not part of that, and must not be absorbed into it.** This is
the one boundary to hold. The worker's tenant scoping, per-user fairness, lease
reclaim and separated query/ingest budgets exist because a naive loop got all of
them wrong, and they were paid for in real bugs. An agent harness knows none of
it. The whole agent loop — many tool calls, several model round trips — runs
*inside* a single queued job. The model decides what to do; the queue still
decides when it runs and on whose behalf. Model orchestration and job
orchestration are different words that happen to share a verb.

**Why open models are load-bearing rather than incidental.** Agent loops make
many more model calls than a single Q&A, so hosted per-token pricing scales
badly with exactly the behaviour we are adding. Tool calling also wants a model
chosen for it rather than whatever an endpoint offers. And on-prem, which is what
closes HIPAA and privilege-sensitive deals, is impossible while inference leaves
the building. Serving our own models is the enabling step, which is why phase 1
comes first.

**Hermes: what we take and what we leave (researched 2026-08-03).**

Hermes is two things with one name, both from Nous Research.

1. **Hermes 4** is a set of open models. You download them and run them yourself.
2. **Hermes Agent** is a program that runs an agent. MIT licensed. Lots of parts
   already built: skills, memory, 60+ tools, MCP, and adapters for Slack, Teams,
   WhatsApp and about twenty other places.

**Decision: take the models, leave the program.**

Here is the whole reason, in one sentence. Hermes Agent puts everybody's memory
in one pile.

Their own open issue says it plainly: *"one agent = one tenant. Memory is
global, sessions don't scope by tenant, and there is no isolation between
groups, channels, or users."* Memory reads and writes skip the hook system, so
a plugin cannot fix it. You would have to fork the project and then own that
fork forever, as a one-person company with paying customers.

That one pile is the exact thing we sell against. A dental practice's records
must never be readable by a law firm. Making that true is what 2026-08-01 to
2026-08-03 was spent on. Putting our agent on a component that does not have it
would throw that away. The same issue thread records an agent reading one
customer's competitor notes and publishing them in a public article, and the
operator nearly being sued.

The official workaround is one process per customer. That throws away the job
queue, per-tenant fairness and lease reclaim, and it does not fit one box.

What we take:

| From Hermes | What we get | Cost to us |
|---|---|---|
| Hermes 4 open weights | A model built for tool calling, run on our hardware | Two env vars, then the benchmark |
| Its tool-call format | vLLM and SGLang already parse it (`--tool-call-parser hermes`) | Nothing. No parser to write |
| The idea of skills | Named procedures over tools, per vertical | We build it inside our own worker |
| MCP support | A standard way to plug in tools instead of a bespoke one | Adopt the protocol, not their runtime |

What we do not take: the agent runtime, its memory, its sessions, its adapters.
We keep our own tenancy, our own queue, and our own membership model, because
those are correct and theirs are not.

**When to look again.** If a `memory:scope` hook lands and the maintainers
commit to real tenant isolation, reassess. The issue is currently labelled
`needs-decision`. Until then this is settled, so do not reopen it without new
evidence from that thread.

Sources, so this can be checked rather than trusted:
`github.com/NousResearch/hermes-agent/issues/34352` (the multi-tenant issue),
`hermes-agent.nousresearch.com/docs`, `huggingface.co/NousResearch/Hermes-4-70B`.

**The model asks. We do the work. (added 2026-08-03, after checking the model
card rather than the marketing.)**

Switching the endpoint to Hermes 4 gives the app no new powers on the day it
happens. The model card is plain: it emits a tool call and *"the actual
execution and response handling falls to the developer"*. It is text only. It
cannot browse, click, or see. The 60+ tools, the browsing and the vision belong
to Hermes Agent and Nous Portal, which we are not using.

What we get is a model that asks for tools cleanly and in a format vLLM and
SGLang already parse. That is the hard part to retrofit and worth having. But
everything the agent can actually *do*, we build.

**Every tool is one of our own functions, wrapped.**

The hard part already exists. `accessible_workspace_ids`, the file search, the
workspace list: each is a function that already knows who is asking and refuses
what they may not have. Making one into a tool is three small things. Describe
it to the model. Run it when the model asks. Hand the result back.

The property that matters: a tool closes over the caller. `search_documents`
cannot reach another company's files because the function underneath already
cannot. A general-purpose tool from somebody else's framework has no idea our
tenants exist. That is the whole reason the tools have to be ours, and it is the
same reason we took the model and left the program.

**Adding a tool, start to finish:**

1. Pick a job a real person does by hand today.
2. Find or write the function. It must take the caller's identity and enforce
   it, like every repository method already does.
3. Describe it to the model: a name, one line on what it does, its arguments.
4. Run it when the model asks, inside the queued job, never outside the tenant
   check.
5. Feed the result back so the model can use it, and so the answer can cite it.

A **skill** is a named bundle of these that a customer recognises as their own
work. "Check this invoice against the contract" is a skill. It is three or four
tools in an order, given a name the customer already uses.

**Where the tool list comes from: the customer, not us.**

Do not invent tools in an empty room. The list is discovered by sitting with a
real business and watching what they repeat. That is what the vertical focus is
for, and it is the part that cannot be bought or copied.

| Vertical | The repeated work to look for |
|---|---|
| Dental | Which consent form applies. What our policy says about this code. |
| Legal | Every clause about termination across these twelve contracts. |
| Accounting | Does this invoice match the contract we signed. |

The rule: **if we cannot name the person who does this by hand today, we are not
building it yet.** A tool nobody asked for is worse than no tool, because it
still has to be maintained and it still widens what the agent can reach.

This is also the honest sales motion. We are not selling an agent that does
everything. We are selling one that does the three things this business does
every week, using their documents, with a citation. That is a conversation to
have with customers, not a feature to guess at.

**Still true, and still the gate on quality:** the 20-question citation benchmark
below. An agent that calls five tools and cites the wrong page is worse than a
single retrieval that cites the right one. Capture the baseline against the
hosted endpoint before any of this starts.

**2. Serving our own models: the three phases (agreed 2026-07-30, reframed
2026-08-03 as the delivery path for the agentic platform above).**

What were tracked separately as a knowledge layer, self-hosting and vision
extraction are one migration: move the model work onto hardware we own, then
spend the now-cheap inference on capabilities that were previously unaffordable.
Sequenced, because each phase depends on the one before it.

**These thresholds are no longer the trigger, and are kept as instrumentation.**
Phase 1 used to wait for hosted inference to pass $500-1,000/month, for vision
pricing to make ingestion uneconomic, for a customer to demand on-prem, or for
latency to become a complaint. That was the right test for a migration whose
only payoff was a cheaper bill. It is the wrong test for the platform the
product is being built on, so the work starts when it is scheduled rather than
when a bill crosses a line. Watch the numbers anyway: they say how urgent it is
and they are the argument for the on-prem edition when a customer asks.

**Serverless GPU was weighed on 2026-07-30 and declined; that still holds, and
matters more now.** Cold starts of 30-120s are tolerable for background
ingestion and fatal for interactive chat, and its single synchronous `/ask`
endpoint would discard the job queue, per-tenant fairness and progress updates.
An agent loop makes that worse rather than better: many model round trips per
job, each paying the same tax. Own the box, or keep a hosted endpoint until you
do, but do not route an agent through cold starts.

**Order of work, corrected 2026-08-03. Tools first, model second.**

The phases below read as though the model swap comes first. It does not, and
doing it first teaches us nothing: with no tools defined, Hermes 4 behaves
exactly like what we run today, because the only thing it adds is the ability to
ask for tools we have not written.

The right order, and the reason for each step:

1. **Build the tool layer against the endpoint we already have.** Verified
   2026-08-03: the current endpoint (`openai-gpt-oss-20b`) already accepts a
   `tools` array and returns well-formed `tool_calls`. A probe asking it to
   search documents came back with `search_documents({"query": "refund
   policy"})` on the first try. So the whole agent loop can be built and
   debugged for zero extra cost, on infrastructure that already works.
2. **Run the loop beside the current pipeline, not instead of it.** Both answer
   the same 20 benchmark questions. Compare citation correctness first.
3. **Only then remove the hardcoded retrieval.** This is the step that carries
   real risk, and it is worth being blunt about why. Today retrieval is
   deterministic: every question searches, at a 3000-token budget, before the
   model sees anything. Handing that choice to the model means it may not
   search, or may search badly, and citation accuracy is the product. Do not
   delete the deterministic path until the benchmark says the agent matches or
   beats it. Keep it behind a flag afterwards.
4. **Then swap the model**, and re-run the same benchmark. Now the comparison
   means something: it measures tool-calling quality between two models on an
   identical loop, which is the actual question.

Written down because the tempting order is the reverse. Swapping an endpoint is
a satisfying afternoon and changes nothing a customer can see. Writing the first
tool is duller and is the whole feature.

**Phase 1 costs nothing to start, and does not need a GPU (priced 2026-08-03).**
Hermes 4 70B is served by OpenAI-compatible providers at roughly $0.13 per
million input tokens and $0.40 per million output. At three seats and a hundred
questions each a day, that is under $20 a month even allowing ten model round
trips per agent job. Renting a 24GB GPU is about $108 a month and would not run
a 70B anyway, so the self-hosted comparison is really a 14B against a hosted
70B. Break-even against owning is somewhere near 270 million output tokens a
month, roughly a hundred times current size.

So phase 1 starts as a **hosted swap**: repoint the endpoint at a provider
serving Hermes 4, run the benchmark, learn whether it is actually better for our
documents. Zero capex, and the same two environment variables either way.

Owning hardware is a **sales decision, not a cost decision**. Do it when a
customer's contract requires that data never leaves the building, price it as a
premium tier, and let their money buy the box. One warning: the cheapest rented
GPUs are marketplaces of other people's machines. Fine for benchmarking, never
for customer documents, since that buys the cost of self-hosting with worse
privacy than we have today.

**Phase 1 — Serve our own models.** `gradient_chat` posts OpenAI-shaped payloads
to `{INFERENCE_BASE_URL}/chat/completions`, and embeddings to
`{DO_EMBEDDINGS_URL}/embeddings`. vLLM serves exactly those, so switching is a
**config change, not a code change**: repoint the two env vars. That portability
was accidental and is now deliberate. Never let provider-specific calls leak past
`llm_service.py`.

This is three models, not one, and that drives the hardware:

| Role | Used by | Today |
|---|---|---|
| Text generation | answers, concept compilation | DigitalOcean endpoint |
| **Vision** | phase 2 page extraction | none yet, new requirement |
| Embeddings | chunk and query vectors | Voyage AI |

Text and vision are separate weights held in VRAM simultaneously, so size the box
for both rather than for the chat model alone. Embeddings are cheap and Voyage is
good, so keeping them hosted is reasonable until the on-prem edition, which by
definition forces all three local. A hosted-hybrid is a legitimate intermediate
state; do not treat self-hosting as all-or-nothing.

**The benchmark is the gate on the whole initiative, not a formality.** A weaker
model degrades three things at once: answer quality in phase 1, transcription
fidelity in phase 2, where hallucinated digits are the worst failure available,
and contradiction detection in phase 3, where being wrong is worse than being
absent. Citation accuracy is the product and it degrades first.

Make it concrete rather than impressionistic:

1. Take roughly 20 real questions against a document already ingested, with known
   correct answers and known correct source pages.
2. Record the current hosted model's answer and cited page for each. This is the
   baseline, and it should be captured **before** any migration work begins.
3. Re-run against the self-hosted model and compare on citation correctness
   first, answer quality second. A prettier answer citing the wrong page is a
   regression.
4. For phase 2, additionally diff the extracted text against the PDF text layer
   on numeric-dense pages.

Model sizing advice dates quickly, so verify current options rather than trusting
a figure written here: as of this writing, very large mixture-of-experts models
need hundreds of GB of VRAM even quantised, which is not a single cheap GPU, and a
mid-sized quantised open model is the realistic starting point. Check what is
current when the work actually starts.

**Phase 2 — Vision extraction for every PDF.** Replace the text-layer, OCR-
fallback escalation with a single vision pass over each rendered page. Removes the
heuristic, the OCR branch, and eventually `pytesseract` and `Pillow`.

`page.get_text("text")` returns characters in PDF storage order, not reading
order: multi-column layouts interleave and tables collapse into soup. IRS
Publication 15, used to test ingestion on 2026-07-30, is exactly that shape. This
is the difference between a citation quoting a coherent passage and one quoting
three columns spliced together. The comparison is not perfect versus risky, it is
Tesseract's errors versus a vision model's, and on dense rate tables Tesseract is
worse.

One safeguard, because it is nearly free: **when a text layer exists, compare the
digits.** If the vision pass and the text layer disagree on a number, trust the
text layer and flag the page. A confidently cited wrong figure in a tax or
clinical document is the one failure that loses a customer. Note that self-hosting
does not reduce hallucination; it addresses cost, privacy and vendor dependence
only.

Determinism is not a real concern here: extraction happens once per document and
the chunks are then fixed, so sampling variance cannot move citations under a
customer during normal use.

**Phase 3 — Compiled knowledge layer.** A distilled, entity-centric layer between
chunks and the query, adapted from the "LLM wiki" pattern with its token-savings
argument discarded: that saving is measured against re-uploading whole documents,
which retrieval at a 3000-token budget already avoids.

What survives is worth more than the saving:

- **Contradiction detection.** A 2023 handbook and a 2025 memo disagree about a
  sterilisation setting. Chunk retrieval returns whichever ranks higher and
  answers confidently, with no notion that sources conflict or that one is newer.
  For dental, legal and accounting, "your documents disagree about X, here is
  both, dated" is a compliance finding, often worth more than the answer. No
  competitor in [[competitive-landscape]] markets this.
- **Cross-document synthesis.** "What is our onboarding process" may span an SOP,
  a checklist and a policy email. Chunks are extractive; concepts merge.

**Concepts route and reason; they are never the citation target.** In the original
pattern answers cite wiki pages. An audited practice needs the citation to resolve
to page 14 of the real protocol, not to a summary a model wrote. Retrieval
consults concepts to decide what is relevant and whether sources conflict, then
pulls the underlying segments. The citation guarantee is the product; do not trade
it for elegance.

Concept bodies are **markdown, not a rigid schema**: a model writes markdown more
naturally than it fills columns, the customer can read and correct their own
knowledge base, and a new section does not need a migration.

```
concepts         id, organization_id, title, body_markdown, summary, embedding
concept_sources  concept_id -> segment_id      (provenance, many-to-one)
concept_links    concept_id <-> concept_id     (relation, incl. contradiction)
```

**Per-workspace compilation rules are a product feature, not a config file.** A
dental practice and a law firm want different emphasis, vocabulary and
contradiction sensitivity. "Your knowledge base follows your firm's rules" is
sellable for very little work.

Compile from the **stored chunks**, never by re-reading the raw file, so a concept
always resolves back to the exact segment it came from. `generate_key_concepts`
already did most of the extraction and deduplication; it fed flashcards, was never
wired to retrieval, and was removed as dead code in `f0a45b2`. Recover the logic
from there rather than rewriting it.

**Why this order.** Phase 3 was originally called the first thing to tackle, but
it depends on the two before it. Compiling concepts from multi-column soup
produces soup concepts, so extraction quality must come first, and concept
compilation is the expensive step that only becomes affordable on owned hardware.
Phase 1 is also the cheapest and least risky, being two environment variables.

**The commercial payoff: an on-prem edition.** Dental (HIPAA), legal (privilege)
and accounting all have buyers for whom "your documents never leave your building"
closes deals hosted inference cannot, and it supports premium pricing. It also
multiplies support burden for a one-person company, since every customer is a
different box, so treat it as a later-stage offering for a small number of larger
accounts.

**What not to do:** move orchestration into a general agent harness. The worker's
tenant scoping, per-user fairness, lease reclaim and separated query/ingest
budgets exist because a naive loop got all of them wrong. Swapping the model
provider is nearly free; rewriting orchestration would discard that and
reintroduce those bugs. Extraction and compilation are model work. The job queue
is not.

*Audio transcription remains a separate product decision.* Whisper would run well
on the same GPU, but video and audio ingestion was deliberately removed on
2026-07-29 and named the largest cost sink. Owning a GPU is not a reason to bring
it back.

**3. Onboarding that makes membership legible from the first screen (raised
2026-08-03, demoted below the agentic platform 2026-08-03).**

Sign-up should ask for the company name and take payment as one flow starting at
the home page. Today it creates a company named after the email prefix, then
sends the person to settings to pay, so the two halves of becoming a customer
happen on different screens with a redirect between them, and the name is
something they discover rather than choose.

The deeper reason is that joining and starting are still easy to confuse.
Somebody sent an invite link ended up creating their own company alongside the
one they were invited to, because every route through a signed-in state with no
organization leads to sign-up. The screens now say which is which (see "Recent
changes", 2026-08-03), and the database refuses a second owned company, so this
is no longer a correctness problem. It is a clarity problem, which is why it is
Tier 2 rather than Tier 0.

Shape agreed with Osas: *sign up* names a company and pays for it; *invite*
means accept, sign in, and land in the company that invited you, never being
offered one of your own; an owner can remove a member; a member can delete their
account freely; an owner must cancel the subscription before deleting theirs.

**Closed 2026-08-07.** All of it is built. The sign-up screen now names the
company and pays for it in one submit; see "Recent changes".

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

## The tenant model (decided 2026-07-29, built 2026-07-31 → 2026-08-01)

**Built.** Organizations exist, subscriptions are keyed to them, workspaces live
inside them, storage mirrors them, and access is answered in one place. What
follows is the model as designed; the differences between it and what shipped are
called out in "As built" at the end of the section.

It was the single biggest structural gap in the product. There was no
organization entity, so `Workspace` did three unrelated jobs at once: billing
entity (`workspaces.user_id` implied who pays), security boundary (membership
granted access), and document container. `Subscription` attached to a *user*, not
a company. Every multi-user defect found on 2026-07-29 traced back to that.

Target model, tenant and objects, in the Entra sense:

| Table | Purpose | Change |
|---|---|---|
| `organizations` | The tenant. Billing entity and security boundary. | **new** |
| `organization_members` | user ↔ org with role: `owner`, `admin`, `member` | **new** |
| `subscriptions` | Keyed to `organization_id`, gains `seats` | re-key from `user_id` |
| `workspaces` | Document container inside an org, many per org | `organization_id` replaces `user_id` |
| invites | Invite to the **org**, not a single workspace | re-scope |
| `files` | `workspace_id` is the boundary; `user_id` becomes "uploaded by" | semantics change |

Decisions already made:

- **Signup creates an organization**, not just a user. You always act inside an
  org, and the switcher is an org-and-workspace switcher.
- **Invites are org-level**, since seats are an org property and membership
  should grant access to the org's workspaces.
- **Org membership initially grants access to all the org's workspaces.** Add
  per-workspace ACLs ("HR docs are managers-only") only when a customer asks.
- **Three roles**, because `admin` is cheap now and expensive to retrofit:
  `owner` (billing plus everything), `admin` (manage members, no billing),
  `member` (use it). This is the admin view versus user view split.
- **Settings splits in two**: Account for everyone (theme, sign out, delete own
  account), Admin for owners only (billing, members, workspace management). Staff
  should not have a billing route to reach, rather than having a hidden button.
- **A person can be staff in one org and own another.** Entitlement is therefore
  **per organization, never a global per-user boolean**. They can be premium in
  one context and free in another simultaneously.

Cases to handle that nothing covers today:

- Staff must **never** be shown a payment form when the *owner's* subscription
  lapses. They get "this workspace's plan needs attention, contact the owner."
- Removing someone from an org leaves their uploads with the org. A departing
  employee does not take the SOPs.

### As built (2026-08-01)

Four things ended up different from the sketch above, each for a reason worth
keeping.

**The roles are `owner`, `admin`, `staff`.** Not `member`, which was a third word
for the same idea and got introduced by accident before being removed. Owner pays
and can do everything. Admin runs the company inside the product but cannot touch
billing, because somebody has to be unable to cancel the subscription. Staff read
and ask, and change nothing: uploading and deleting are the same permission as
managing, since a knowledge base nobody may add to does not need a role of its
own. A migration rewrote the existing `member` rows; the word is still accepted as
a synonym so an old row resolves, and nothing writes it.

**Permissions are a capability table, not role strings.** `api/core/permissions.py`
holds one map from role to capabilities, consulted everywhere. Role comparisons
used to be written inline at nineteen call sites, so adding a role meant finding
all nineteen and missing one meant a silent grant or a silent denial. Adding a
role is now a row; adding a capability is an enum member plus the roles that hold
it. Neither requires touching a route.

**Reach is a per-member property, not a second kind of invite.** An
`organization_members.scope` of `organization` sees every workspace in the
company; `workspace` sees only what has been assigned. So a person is invited to
the company once and the owner turns their visibility up or down afterwards,
rather than the invite deciding forever. `accessible_workspace_ids` is the single
answer to "what may this person see", and documents, retrieval, conversations and
the workspace picker all ask it. Most bugs in this area were some other function
being asked that question, `list_workspaces_for_user` ("what do you own") seven
separate times.

**Storage mirrors the tenant.** Objects live at
`workspaces/{workspace_id}/{file_id}-{filename}`. Deleting a workspace deletes a
prefix, deleting a person touches no storage at all, and renaming a workspace is
free because the path is keyed by id. The file id leads the name because the
folder is shared: without it, two people uploading `invoice.pdf` into one
workspace wrote the same object and the second silently replaced the first. A
document may also carry only one name per workspace, refused at upload, because
two files with one name make a citation ambiguous.

Documents are private. `files.file_url` is a stable identity and is deliberately
not fetchable; reads go through a 30-minute signed URL minted per request after
the caller's access is checked. Before this, every uploaded document was
world-readable to anyone holding the URL.

**Still open:** per-document ACLs. Deliberately not built. Organizing sensitive
material into its own workspace covers the cases seen so far, and per-document
access is a fourth boundary to keep correct for a demand no customer has stated.

### Pricing (agreed 2026-07-29, site copy not yet updated)

Moving to base plus included seats plus overage. Headline prices unchanged:

| Plan | Now | Agreed |
|---|---|---|
| Starter | $99, up to 10 members | **$99, 10 seats included, +$9/seat** |
| Business | $249, unlimited members | **$249, 30 seats included, +$7/seat** |

Reasoning worth preserving: pure per-seat suppresses the adoption that makes a
knowledge base sticky, because the buyer declines to add the receptionist and
then the receptionist keeps interrupting people, which is the problem the
product sells against. Marginal cost tracks documents and queries, not headcount,
so seats are a value metric and should be priced cheaply. But "unlimited" at
$249 caps revenue exactly on the largest, best-served accounts, which is the
real leak. 30 included covers most of the 10-50 target, so the overage catches
outliers rather than nickel-and-diming the median.

Also agreed: annual prepay at two months free, and seat removal must be instant
and self-serve so customers trust the overage.

Shipped. Two graduated tiered prices, not the four originally sketched: one
price per plan whose first tier is a flat amount covering the included seats and
whose second charges per seat beyond them. A base item plus a separate per-seat
item would have meant two quantities to keep in step; this way the subscription
item's quantity is simply the headcount and Stripe does the arithmetic, which
also produces an invoice a customer can read.

`api/core/plans.py` is the single definition of the amounts, and the Stripe
prices were built to match it. A Stripe price is immutable, so changing an
amount means creating a new price and repointing `STRIPE_PRICE_ID_<PLAN>`.
Quantity is synced on invite accept and member removal, both invoiced
immediately so an added seat is not free until renewal and a removed one stops
costing money at once. The sync never raises: the membership change has already
happened, and drift is corrected by the next sync or the webhook.

The trial was dropped rather than built. Access is bought with a card, or
granted by being invited into an organization that already pays. There is no
third path: a billing-exemption flag was built and then removed, because it was
a second way to be entitled that would have needed testing forever and could
only be exercised in production. Everything is now verifiable end to end in
develop, where Stripe test mode makes subscribing free.

**There is no free tier, and as of 2026-08-01 there is no code for one.** An
organization has a subscription or it has nothing. Subscribed means the plan's
seats apply and nothing else is capped; unsubscribed means no app at all, and the
only refusal is `SUBSCRIPTION_REQUIRED`.

This had to be removed rather than left dormant. `api/core/limits.py` still
enforced five documents, 500 MB and one workspace, and the UI still carried an
upgrade banner, a usage meter and a create button that disabled itself. None of
it could fire for a customer arriving through the product, because signup takes a
card and an unsubscribed organization is bounced to billing before it reaches the
app. What it could do was fire against a *paying* company whose status had not
loaded, which is exactly how it was found: an owner looking at "Free plan: 1
workspace. Upgrade for more!" on an active subscription. Dormant code that can
only run when something else is already broken makes the broken thing harder to
see.

The one real state left is a lapse. `SubscriptionNotice` says the plan needs
attention and points at settings; staff are never shown a payment form for
somebody else's subscription.

Deliberately still uncapped: storage per organization. Per-file is 100 MB and
uploads are rate limited, so this is not urgent, but if a ceiling is ever wanted
it belongs in `plans.py` as an attribute of a paid plan, not as a revived free
tier.

Granting an account access outside the normal flow is a database operation, not
a product feature: insert a subscription row for the organization with status
'active'. Deliberately not an endpoint, so it cannot be reached by a bug.

Not doing yet: usage-based query pricing. It tracks cost best but makes bills
unpredictable, which SMBs punish. The TIMING logs will reveal a runaway account;
answer that with fair-use limits rather than repricing everyone.

## Recent changes (chronological, most recent first)

- **2026-08-07 (settings names the plan):** The billing panel said "Your
  subscription is active" and nothing else, so an owner could not tell Starter
  from Business anywhere in the product, including when working out whether they
  had run out of seats.

  Nothing needed to be computed. `plan_key` and `seats` were written on every
  subscribe and by the webhook's plan sync, and `get_subscription` simply did
  not copy them out of the ORM row, so no caller could see them. Added there,
  surfaced by `/subscriptions/status` as `plan`, `plan_name` and
  `seats_included`.

  The name is resolved by the backend through `get_plan`, not mapped from a key
  in the browser: the names and prices live in `core/plans.py` next to the
  Stripe price ids they were created from, and a second copy of that mapping in
  the frontend is how a page ends up naming a plan the customer is not on.

  Reads "You are on Business, 30 seats included." One line rather than two,
  because the organization section below already shows "1 member of 30 seats"
  and two numbers saying 30 on one screen is noise.

  Verified against both live subscriptions: org 1 returns starter/Starter/10,
  org 4358 returns business/Business/30, and the panel renders the Business one.
  A contradictory "this organization has no plan yet" banner seen alongside it
  turned out to be an artifact of the dev sign-in harness, which skips
  organization selection; once the organization resolves the banner is gone.

- **2026-08-07 (an unpaid organization could still ask questions):** Found by
  checking a condition rather than asserting it. The signup flow above keeps the
  company when a card is declined instead of rolling it back, on the grounds
  that settings can take the card later; Osas accepted that "as long as the
  unpaid company has no access". It had access.

  **Uploading and creating a workspace both went through `_assert_subscribed`.
  Asking a question did not.** `create_message` checked who you are and what you
  own, and nothing else. Verified against a real unpaid tenant, not read off the
  code: creating a workspace returned 402 `SUBSCRIPTION_REQUIRED`, and the same
  account posting a message returned 201 and ran the entire pipeline. From the
  worker's own log, `TIMING {"event": "query", "ms": 7348, "chunks": 0}` — 7.3
  seconds of retrieval, coverage loop and generation, including the metered
  model call, for an organization that had never paid.

  **Why it survived this long.** An unpaid tenant looks harmless. It cannot
  upload, so it has no documents, so every answer is "I couldn't find relevant
  information in your documents." Nothing leaks and nothing looks broken. It
  only costs money, quietly, per question, and it means an account that never
  paid still gets to use the product.

  Closed with `assert_can_ask` in `core/limits.py`, the same shape as
  `assert_can_create_doc`: the governing organization is the workspace's, not
  the asker's, so staff stay covered by the company that pays for them. Called
  after the ownership checks and before anything is written or queued, so a
  refused caller stores no message and spends no model call. Deliberately not
  first: a history belonging to somebody else answers 404 whether or not the
  asker pays, rather than confirming it exists.

  Verified: unpaid asks → 402, and the message count in that history is
  unchanged after the attempt; the same organization marked active → 201 and the
  answer runs. 86 tests pass.

- **2026-08-07 (signup names the company and pays for it, on one screen):**
  Closes Tier 2 item 3, which was held for "before real marketing spend". That
  condition became true this week.

  **What it was.** Pressing sign up parked `auth_intent=signup`, and the
  sign-in listener created the organization the moment Firebase reported the
  account, named `email.split("@")[0] + "'s Organization"`. Then a redirect to
  settings to pay. So becoming a customer happened on two screens, and the
  company arrived already named something they never chose but their colleagues
  would see in the chooser and in every invite email.

  **What it is.** One form: company name, plan, card, one submit. It creates the
  organization with the chosen name, subscribes it, handles a 3D Secure
  challenge if the card needs one, and lands in `/chat`. The listener no longer
  creates anything: `auth_intent` is simply not set on this path, so it
  registers the user and stops.

  **`POST /users` takes an optional `company_name`.** Optional because the same
  endpoint is called by the sign-in listener with nothing to say, and the
  derived name stays as the fallback so a blank field cannot stop somebody
  buying. `SignUpRequest` is defined at module level, not between the decorator
  and the handler, which registers the route against the model and crash-loops
  the app on boot; that has happened here once already.

  **Two things extracted rather than copied.** `services/subscribe.ts` holds the
  subscribe-and-confirm sequence, because two screens now take a card and the
  awkward part is 3D Secure: four steps, two failure modes, and a copy on each
  screen would drift, with the untested screen drifting first and the symptom
  being a customer told their card was declined when it was not.
  `components/PlanPicker.tsx` holds the prices, because two copies eventually
  advertise two different numbers. `PaymentView` now uses both and got shorter.

  **A retry after a declined card would have lied.** The organization is created
  before the charge, and an account owns exactly one company, so posting the
  name again returns the existing organization unchanged and an edited name
  would be silently ignored. The screen now remembers the id, skips creation on
  the retry, disables the name field and says the company already exists.

  **Found by driving it:** the card field mounted with no border or padding and
  was invisible, because `PaymentView.css` scoped `.StripeElement` under
  `.PaymentView` and signup is not inside it. Only a screenshot showed this;
  every type check passed.

  Verified end to end against Stripe test mode on both paths: signup created
  "Northgate Dental Group" (the typed name, not the derived one), active on
  starter, landing in `/chat`; the settings path still subscribes after the
  refactor; and `POST /users?intent=signup` with no body at all still produces
  the derived name. 86 tests pass, tsc clean, test data removed from the
  database, Stripe and Firebase.

- **2026-08-07 (the second John Smith could not sign up):** Found while testing
  the 3D Secure work, not looked for. Two throwaway test accounts happened to
  share a display name and the second signup returned 500.

  **`users.username` is not a username.** Nobody chooses it and nobody types it.
  It is the `name` claim off the Google sign-in token, a display name, and it
  carried a UNIQUE constraint. So the second person named John Smith to ever
  sign up could not. `add_user` hit the constraint, returned None, and
  `POST /users` turned that into `500 Could not create user.` They saw a generic
  error, got no account, and no retry helped, because the name comes from their
  Google profile. Silent, permanent for that person, and more likely with every
  signup. Live in production the whole time; the cold email campaign starting
  this week is what made it urgent.

  Reproduced against the real table before changing anything:

      add_user('alice.smith@…', 'John Smith')  -> 7395
      add_user('bob.smith@…',   'John Smith')  -> None

  **The constraint protected nothing.** Identity is the email, which has its own
  unique index and is what `get_user_id_from_email` uses to turn a request into
  a user. `username` is read in exactly one query in the entire codebase, the
  "who would be stranded" list in `async_workspace_repository`, as a label for a
  human, already written `COALESCE(NULLIF(u.username, ''), u.email)` because it
  was never trusted to be meaningful. Dropped in migration
  `20260807_username_not_unique`. NOT NULL stays: signup substitutes the email
  when the token carries no name, so the column is always populated.

  **The downgrade will fail if two people now share a name, deliberately.**
  There is no safe automatic way back, and deleting one of two real accounts to
  restore a cosmetic index would be worse than a failed downgrade.

  **Two things fixed alongside, because they are why it stayed invisible.** The
  log line said "User with email X or username Y already exists", naming two
  possibilities and committing to neither, so the message never pointed at the
  constraint that was actually firing; it now names the violated constraint. And
  `add_user` returned None for every IntegrityError, including the race where
  two requests for the same new account cross between the caller's existence
  check and the insert; it now re-reads by email and returns the id the winner
  created, the same shape `_start_organization` already uses. That re-read is
  deliberately outside the session block, since taking a second connection while
  holding the first is how the pool runs dry under the exact burst that causes
  the race.

  Verified end to end through the real route: two Firebase users sharing the
  display name "John Smith" both signed up, 201, separate users and separate
  organizations. 86 tests pass, single alembic head.

- **2026-08-07 (3D Secure: the cards that could never pay):** Closes the last
  open Tier 0 item, found in the 2026-07-29 Stripe review and left alone since
  because it needed live test-mode iteration rather than a guess.

  **The failure.** A card that requires authentication does not decline. Stripe
  accepts the subscription, leaves it `incomplete`, and waits for the cardholder
  to pass their bank's challenge. `create_subscription` returned whatever status
  Stripe gave it and stopped there, and `PaymentView` classified the card-update
  branch as *anything that is not one of five known-good statuses*, which swept
  up `incomplete`. So the customer was shown "your payment method needs to be
  updated due to an expired card or other issue", entered the same working card,
  and got the same message. Common on EU and UK cards, and spreading.

  **`latest_invoice.payment_intent` is the wrong field, and fails silently.**
  Every guide reaches for it. On this account's pinned API version
  (`2026-07-29.dahlia`, from stripe-python 15.4.0) it is **null** — removed in
  the Basil-era invoice rewrite. Expanding it costs nothing, raises nothing and
  yields nothing, so the code would have looked correct and never found a
  secret. Verified by probing test mode before writing any of this:
  `latest_invoice.confirmation_secret.client_secret` is where it lives now. This
  is the single most useful thing on this entry; do not "fix" `_client_secret`
  back to the documented field.

  **`requires_action` cannot be derived from the secret's presence.** First cut
  returned `requires_action = bool(client_secret)`, which testing the *ordinary*
  card immediately disproved: a payment that succeeds outright comes back
  `active` **with a confirmation secret attached**, so every normal customer was
  being sent through a `confirmCardPayment` round trip against an intent that had
  already succeeded. Gated on `status == 'incomplete'` instead. Only found
  because the non-3DS path was retested after the 3DS path passed.

  **A confirm endpoint, not a wait for the webhook.** `customer.subscription.updated`
  already carries incomplete → active and the webhook already handles it, but it
  arrives on Stripe's schedule and the browser asks "am I in?" the instant the
  popup closes. Reading our own row at that moment still says `incomplete`, so a
  customer who just authenticated correctly gets bounced back to the payment
  screen. `POST /subscriptions/confirm` asks Stripe directly. Verified against
  the real race: Stripe `active`, database `incomplete`, no webhook able to reach
  localhost, and the endpoint healed the row. Idempotent, since it writes
  whatever Stripe currently says.

  **The retry guard had to ask Stripe, not the row.** Retrying after an abandoned
  challenge used to stack another `incomplete` subscription each time, so that got
  a "cancel the stale one first" guard — which, keyed off our own row, would have
  cancelled a *paying* customer whose webhook was still in flight and charged them
  again. The row is a cache; Stripe is the fact. It now retrieves first, and when
  Stripe says active it refreshes the stale row and refuses the second sale.
  Tested in exactly that mismatched state: no duplicate, nothing cancelled, row
  healed.

  **Found while reading: the card-update branch had never worked.** Three
  independent defects, any one fatal. `clientSecret` was declared and never
  assigned, so `confirmCardSetup('')` failed on contact and no SetupIntent
  endpoint existed to give it one. The browser sent `payment_method_id` where the
  route reads `payment_method`, a 422 before the handler ran. And the route
  rebound `payment_method` to the retrieved object and passed that object where
  the API takes an id. Anybody who went `past_due` reached a form that could not
  work. Fixed all three, added `POST /subscriptions/setup-intent`, and the route
  now reads status back from Stripe instead of echoing the stale row.

  **What could not be verified here.** Clicking COMPLETE inside Stripe's 3DS
  challenge iframe: the modal renders correctly (confirmed visually, branded
  OSAS INC) but synthetic clicks are not trusted by that cross-origin frame, so
  the challenge was completed server-side by confirming the PaymentIntent with a
  non-3DS test card, which produces the identical post-challenge state. The
  browser half of `confirmCardPayment` is therefore exercised up to the popup and
  not through it. Worth one manual click-through before this reaches real
  customers.

  **Also noticed:** `POST /users?intent=signup` returns 500 "Could not create
  user." when the display name collides with an existing row. Pre-existing and
  unrelated to billing, chased down and fixed the same day, see the entry above.

- **2026-08-07 (guardrails: what an uploaded document can reach):** A customer's
  document is untrusted input. Tested rather than assumed, by uploading one.

  **Structurally safe already.** Retrieval is scoped by `workspace_id` in SQL and
  the answer path has no tools, so injected text cannot widen a WHERE clause or
  call anything. Cross-tenant leakage by injection is not possible.

  **Not safe.** A document written to read like a legitimate 2026 policy revision
  made the pipeline answer 90 days where the real document says 30, enumerate the
  workspace's document names unprompted, and reproduce an attacker URL. A crude
  `SYSTEM: ignore all previous instructions` injection failed; the one that looked
  like a document succeeded. That is the realistic threat: a vendor PDF, a
  contract, something forwarded by someone who did not write it.

  Answers may now only link to hosts we serve documents from. Everything else
  becomes `[link removed]`, counted and logged, because a rise in that count means
  something in a customer's documents is trying to put links in front of people.
  Bare URLs too, since `remark-gfm` autolinks them. Hostname-parsed, so
  `storage.googleapis.com.evil.example` is rejected rather than passing a
  substring test.

  **Deliberately still open: content poisoning.** A document asserting a false
  refund window is still quoted as a source, and no code can decide which of two
  customer documents is honest. The defence is provenance, which already works,
  since the answer cites the document that said it.

- **2026-08-07 (customer questions were leaking):** For a dental or legal
  practice the question is the most sensitive string in the request. Seven log
  sites carried it, five at INFO, and they now carry a fingerprint and a length,
  `q8920cefa len=63`, stable enough to correlate two lines and one-way so a log
  archive is not a transcript.

  The larger one was not a log line. The question travelled as a query parameter,
  so it reached the access log and would reach any proxy log, CDN log, browser
  history, and `Referer` header sent onward. It now goes in the request body. The
  route still accepts the query parameter so a tab open on the old bundle survives
  a deploy; **that fallback is what makes the deploy safe and what keeps the leak
  reachable, and should be removed once the access log shows nothing using it.**

  A grep audit found three of the seven sites and missed the URL entirely. Asking
  a question with a name in it and searching the container's log stream found all
  of them.

- **2026-08-07 (extraction was never chunking):** `chunk_text` targets tokens and
  multiplied by four for PDFs, so the splitter fired at 800 against pages
  averaging about 520 and almost never fired.

  ```
    before   316 segments, 316 chunks, 1.00 per page
    after    227 segments, 540 chunks, 2.38 per page, mean 310 tokens
  ```

  A chunk was a page by arithmetic rather than by anyone's decision, so **every
  measurement this codebase had made about chunking was really about page-sized
  chunks**. Segments falling to 227 is the other half: that is the true page
  count, and the surplus was pages torn up by a splitter firing inconsistently,
  which is also why `(file, page)` was not a unique key.

  Retrieval units and citation units are now separate. A chunk is retrieved; a
  page is cited, because a page is what a reader opens. Old rows keep a null chunk
  content and fall back to the segment, so they keep working until re-ingested.

  Measured, three runs each: **18.0 → 19.0 of 27**, and with retrieval per
  information need on re-chunked documents, **21.0**. That last setting stays off
  by default: the same loop measured 15.3 against 17.0 on page-sized units, and
  every document uploaded before this is still page-sized. Raise `MAX_RETRIEVALS`
  to 3 per deployment once its documents have been re-ingested.

  **A regression shipped in the middle of this.** Adding `page_text` to the three
  processors used the PDF processor's variable name in all three; the other two
  call it `section_content`. Every section of every `.docx` and `.txt` raised
  `NameError`, was caught per section as designed, and the file was marked
  processed with zero chunks. It looked ready in the list and answered nothing.
  The verification that missed it used PDFs only. A processor that extracts
  nothing now fails the file, because this shape had shipped twice and both times
  the only trace was a log line nobody reads.

- **2026-08-05, later (one pipeline that sometimes loops):** There were two
  systems calling the same `hybrid_search` and scoring 17.0 and 16.2.
  Everything separating them was the code around it, and three of the
  session's regressions came from that code drifting apart:

  | | |
  |---|---|
  | citations 8/22 | the agent transcribed `file.pdf, page 15` while the pipeline emits `[Segment N]` and resolves it in code |
  | ranking lost | the agent accumulated retrieval its own way and discarded the global ordering the pipeline gets for free |
  | 0.83 / 0.17 | a silent fallback made a benchmark run measure a mixture of two systems and report one number |

  The graph is now one path with an optional loop:

  ```
  question -> needs? -> retrieve -> accumulate -> coverage -+- gap  -> retrieve
                                                            +- done -> answer
  ```

  A single-need question takes exactly the path the shipped pipeline takes.
  A question joining two things gets a retrieval aimed at each.

  **Coverage is ensured, not inferred.** The first attempt credited the broad
  opening search with covering every need, so the loop existed and could never
  fire. No model decides whether a need is satisfied: that judgement was tried
  as an evidence selector and regressed the agent from 16.2 to 11.2 while
  looking perfectly reasonable on a four-question spot check.

  **Decomposition is gated by regex before any model is asked.** Without it the
  model split "how long do i keep tax records" into two needs and turned one
  lookup into three retrievals. All five multi-document benchmark questions
  contain a coordinating conjunction; only three of seventeen single-document
  ones do. The guard removes a model call rather than adding one.

  Deleted: `tool_agent.py`, `document_tools.py`, `AGENT_MODE`, and the
  duplicate citation and answer paths. Net 804 lines fewer.

- **2026-08-05 (what the measurements were actually worth):** Several
  conclusions reported during this work were wrong, and the corrections are
  worth more than the conclusions.

  Three prompt variants were compared at one run each before anyone checked
  whether the benchmark could see a difference that small. Identical code then
  scored 17/25 and 16/25 with five questions flipping. `--repeat N` now reports
  the spread and states how large a change has to be to count.

  A per-document diversity cap looked obviously right and lost at all nine
  settings it was swept at. Contextual retrieval, the highest-expected-value
  item from the literature, measured neutral to slightly negative here, because
  its gains restore context that chunking destroys and our chunker almost never
  fires: a chunk is a whole page already.

  The agent's multi-document advantage, reported twice as the strongest
  argument for the direction, was a two-run artifact. At four runs it is 1.8
  against the pipeline's 1.8.

  **Five multi-document questions is too thin a sample to steer by.** Two
  confident claims came from it and neither survived four runs.

- **2026-08-05 (backend modernization: six items, three surprises):** A review
  of the backend found six things worth changing. Doing them found three
  things nobody was looking for.

  **The event loop was never concurrent.** `llm_service` used `requests` and
  `time.sleep` while every caller was an `async def`. The worker declares
  `asyncio.Semaphore(QUERY_CONCURRENCY=4)` and starts a task per query, so it
  believed it ran four at a time; a blocking POST with a 120-second timeout
  stopped the loop, so it ran one, and nothing else on that loop ran either.
  Now `httpx.AsyncClient` end to end: **2.6x on four concurrent calls**,
  measured warm so the figure is not just TLS setup.

  **There was no vector index.** None: the only entry on `chunks` was its
  primary key. Every question scanned every chunk and re-tokenised every
  segment. Adding an index alone would have done nothing, because
  `ORDER BY 0.7*vector + 0.3*text` is not a distance and no index describes
  it. The query was reshaped into two indexed searches fused by reciprocal
  rank: **140-204ms → 10-20ms, and recall 18/21 → 19/21.**

  **There has never been a temperature.** Every call ran at the endpoint's
  sampling default. Four runs of identical code scored 12-18 of 22. At 0.1 the
  spread halves to 13-16 with the mean unchanged, which is the expected shape.
  A customer asking the same question twice was getting different pages.

  Also: `format_user_chat_history` had raised `NameError` on every call since
  `fafc428`, swallowed by its own handler, so **conversation history has never
  reached the model** and every question was answered as if it were the first.

  **What did not work, and is recorded so it is not tried again.**
  Contextual retrieval, the highest-expected-value item on the list, measured
  neutral to slightly negative here (@5: 17/21 without, 16/21 with). The
  reason says when to revisit: the gains come from restoring context that
  chunking destroyed, and our chunker almost never fires, so a chunk is a
  whole page already coherent under its own heading. It belongs with small
  chunks. Kept behind `CONTEXTUALIZE_CHUNKS`, off, with a backfill and a
  `--strip` that undoes it, because a change to ingestion that cannot be
  turned off cannot be tested.

  **Deleted:** `rag/` scaffolding (a pipeline holding two classes, a factory
  building the pipeline, interfaces with one implementation each, a search
  engine imported by nothing), and `query_chunks_by_embedding`, a
  pre-pgvector search that pulled every one of a user's embeddings into Python
  to loop over. Those four lines were the only reason the API depended on
  numpy, scipy and scikit-learn; all three are gone from requirements.

- **2026-08-05 (the model would not navigate, so it was handed the map):**
  Extraction opened every PDF with fitz, read its pages, and closed it without
  asking what it contained. Three of five benchmark documents carry a real
  embedded table of contents, 118 entries in one. All discarded.

  That absence is why the ADA failure survived three retrieval levers: asked
  whether a shop must be wheelchair accessible, retrieval returned page 8,
  which uses "readily achievable" while explaining parking, rather than page
  6, where the rule is defined. Both contain the phrase and no ranking
  function can tell which is the section about it.

  Outlines are now extracted at upload, from the document's own contents where
  it has one and from type size where it does not, with a backfill for
  documents already uploaded. `outline()` and `search_within()` are tools.

  **The model then ignored them: zero calls, exactly as it ignored
  `read_page`.** That is three prompt revisions that changed nothing. So the
  contents pages of every document now go into the system prompt instead. At
  SMB scale that is affordable and at web scale it is not, which is the point:
  five documents and 281 headings is about 4k tokens against a 120k window. It
  matches the finding from the literature that small open models want more
  structure, not more freedom.

- **2026-08-04 (the pipeline was broken, not badly tuned):** The first
  benchmark baseline scored 10/25 with 11/21 citations, and three questions
  answered *"I couldn't find enough evidence in your documents"*. Running
  `hybrid_search` by hand for those three put the correct page at rank 1,
  rank 4 and rank 1. Retrieval had found them. Every stage after it threw
  them away.

  Six defects, none of them a tuning knob:

  | what | effect |
  |---|---|
  | `CrossEncoderReRanker` re-embedded `content[:600]` with the same bi-encoder that made the score it claimed to improve | deleted correct pages; the ADA guide states its 15-employee threshold at character 2460, and the first 600 are phone numbers |
  | `chunk_selector.select()` ran on its 3000-token default | ~6 of 15 chunks reached a 120,000-token window |
  | `plainto_tsquery('simple', …)` ANDed stopwords | matched **zero rows**; the keyword half of "hybrid search" had never worked |
  | `1 - (emb <-> emb)` used Euclidean under a cosine formula | negative scores, two halves with no shared scale |
  | `text_chunk[:max_context_length]` | a token budget slicing characters |
  | the citation gate | told customers their document was empty when the answer was at rank 1 |

  Result: citations **11/21 → 16-18/22**. The reranker is deleted, not fixed.

  **A per-document cap was built, measured, and thrown away.** A 98-page
  handbook was taking 14 of 15 slots, so capping its share looked obviously
  right. Swept at top_k 15/25/40 against caps of off, /3 and /4, uncapped won
  or tied at every single point. `top_k` went to 25 instead. Room, not
  rationing. The sweep is in the commit; the cap is not in the code.

- **2026-08-04 (the benchmark had to be made honest before it could be used):**
  Three things were wrong with the instrument, and each one had already
  produced a false conclusion.

  **It could not see its own noise.** Two runs of identical code scored 17/25
  and 16/25, five questions flipping. Three prompt variants had already been
  compared at one run each and reported as improvements and regressions; none
  of those comparisons could see what they claimed. `--repeat N` now reports
  the range and states outright how large a change has to be to count.

  **It was wrong about its own corpus.** Question 24, "how do i register a
  trademark", was written as a refusal on the note *"plausibly small-business,
  genuinely absent"*. The SBA guide has a `Trademarks/Service Marks` section on
  page 10. Nobody checked, which is the exact failure the file's own header
  warns about. Found by the tool agent, which searched, quoted the passage, and
  added that the guide gives no step-by-step procedure. Only one of the four
  refusal questions had ever been verified; all four now are, and the new one
  was checked against the corpus before being written.

  **It scored typography.** A correct answer failed for writing
  `self‑inspection` with U+2011, and a citation was read as absent because the
  marker came back as `[Segment 1]` with U+202F. Rescoring the original
  baseline answers with the fixed scorer gives 14/25 rather than 10/25 with
  citations unchanged at 11/21, which is the point: **the scorer fix bought no
  product improvement and is not counted as one.**

  It also scored an expired Firebase token as a catastrophic regression: a full
  three-run sweep returned 0/26 because every request was a 401. One cheap
  authenticated call now runs first.

- **2026-08-04 (tools: built, measured, switched off):** `AGENT_MODE=tools`
  gives the model `search_documents`, `read_page` and `list_documents` and lets
  it decide how often to call them. Same benchmark, same corpus, two runs each:

  ```
                        citations        refusals   overall
    fixed pipeline      16-18/22 (17.0)   3/4       18-20/26
    tool agent          13-14/22 (13.5)   3-4/4     13/26
  ```

  The pipeline wins by more than either side's noise, so the flag defaults to
  `pipeline` and this ships off. It is kept because two things it does are new:
  refusals reached **4/4** for the first time, and question 17 cites both OSHA
  and the ADA guide where the fixed pipeline retrieves fourteen OSHA chunks and
  one ADA chunk.

  **Citations are verified, not trusted.** The model names a page; the code
  checks that claim against the passages the tools actually returned and drops
  any citation to a page it was never shown. The first run scored 8/22 because
  the model wrote "Publication 583, page 12", which names no file and cannot
  become a link, so every citation was discarded **in silence**. Each passage
  now carries the exact string to copy, and drops are counted and logged. That
  one fix moved citations from 8 to 13-14.

  **The model never names a workspace.** Scope is bound once from the
  authenticated request and the tool schemas have no field for it, so an
  instruction hidden inside an uploaded PDF has nothing to address.

  **The obvious explanation was tested and is wrong.** The tool agent sees 8
  passages per search where the pipeline puts 25 in front of the model at
  once, so the gap looked like context width rather than architecture. Raising
  `TOOL_SEARCH_RESULTS` to 20:

  ```
    per search   citations        refusals
      8          13-14/22 (13.5)   3-4/4
     20          12-13/22 (12.5)   2/4
  ```

  Slightly worse on citations and clearly worse on refusals, which matches
  what the fixed pipeline showed earlier: more context makes a 20B model more
  willing to answer from adjacent material. The default stays 8. **The
  remaining gap is the loop, not the width of what it sees**, so the next
  attempt has to change how the model decides, not how much it is handed.

  A concrete lead: asked whether a shop must be wheelchair accessible, it
  cited ADA page 8, accessible parking, rather than page 6, where "readily
  achievable" is defined. It takes the first passage matching a phrase instead
  of the page that defines the concept. `read_page` exists to fix exactly that
  and the model is not reaching for it.

  **Extraction was never the problem.** IRS Table 3 extracts cleanly, header
  and all. The only unicode fault was in the scorer.

- **2026-08-03, later (an invite says what somebody will be):** Every invite
  produced an organization-wide staff member, because that is what the accept
  path hardcoded. An owner who meant to add an admin, or to confine somebody to
  two workspaces, let them in with more access than intended and then corrected
  it on another screen. The invite now carries role (`staff` or `admin`) and
  reach (`organization`, or a chosen set in `workspace_ids`), both still
  changeable afterwards in the members list.

  **Scope applies to admins now.** It did not before: `admin` implied the whole
  company whatever the column said, which made "confine this admin to two
  workspaces" impossible to express. Admin describes what somebody may do, not
  how much they can see. Owner stays unbounded, because the company is theirs.

  **And the read and write paths disagreed about it.**
  `accessible_workspace_ids` hid the workspaces a confined admin had not been
  given, while `get_user_role_in_workspace` still returned `"owner"` for any
  admin regardless of scope. So they could not see Payroll and could upload into
  it by naming its id, holding *owner* capabilities while doing so. Seeing and
  doing are two questions and only one had been tightened. This is the third
  time that shape has produced a bug (see `list_workspaces_for_user`, and the
  `role`/`can_manage` conflation): when a rule changes, find every function that
  answers a version of the same question, not just the one that was reported.

  Also: workspace ids in a request body are checked against the organization
  doing the inviting, so an owner cannot grant their invitee access to a
  workspace in somebody else's tenant. The partial unique index is declared on
  the model as well as in its migration, because autogenerate compares the
  database against the models and would otherwise propose dropping it, silently
  restoring the race that gave one email two companies. And the invite email
  sends the link as text: a styled button was reported as "this site doesn't
  support a secure connection" while the same URL pasted into a browser worked,
  which is what mail clients rewriting link targets through their own
  redirectors does.

- **2026-08-03 (the day live Stripe was exercised, and what it cost):** The
  payment path ran against real money for the first time. Almost everything that
  broke was invisible in test mode.

  **Seats had never been billed at all.** `seats.py` called `item.get("quantity")`
  on a StripeObject, which raises AttributeError rather than returning a default,
  inside a function that swallows exceptions on purpose. So the sync failed at
  its first line every single time and logged it as a Stripe problem. An
  organization with two members sat at quantity 1 while every call site believed
  it had synced. Found by comparing a live subscription against its member count.
  A dict-like object that is not a dict, inside a function designed never to
  raise, is how a revenue path stays broken without anyone seeing it. `_read()`
  in `subscriptions.py` now exists so nothing reads a Stripe object any other way.

  **And the seat sync fired on the path almost nobody takes.** It ran on the
  explicit accept-invite route, but signing in accepts every invite waiting for
  the address, so by the time the link is revisited the token is spent and that
  route never runs. Members who joined the ordinary way were free forever.

  **One email owned two companies.** Signing up with Google fired two
  `POST /users?intent=signup` at once — the auth listener sends one, and the
  sign-up screen sent another after the popup closed. Both asked whether the
  person already owned a company, both asked before either had inserted its
  membership row, both created one. The subscription then attached to whichever
  the person happened to be standing in, which is how $99 landed on an
  organization the customer was not looking at. The duplicate call is gone, and
  a partial unique index on `organization_members (user_id) WHERE role = 'owner'`
  makes it impossible rather than unlikely.

  **Deleting an account destroyed a paid subscription, silently.** The task
  cancels the Stripe subscription, deletes the customer and deletes every
  organization the person solely owns, and refunds nothing. An account deleted
  four minutes after paying took the whole month with it, with the cancel button
  unused on the same screen. That is where the $99 went. Deletion is now refused
  with a 409 while an owned company still pays. Refusing is the only option that
  moves no money on its own, and it makes the order deliberate. Staff are never
  blocked, since somebody else's subscription is not theirs to be trapped by.

  **Entitlement answered about the person when asked about a company.**
  `resolve_entitlement` asks "does any company of yours pay", which is right for
  an unscoped call and wrong for a scoped one, so an owner of a paying company
  was told their unpaid second company was entitled — "no plan yet" in the
  banner, "your subscription is active" in the panel below.
  `entitlement_for_organization` answers about the company actually named.

  **And `organization_id` on that endpoint was never checked**, so any signed-in
  person could read any company's subscription status and renewal date by
  guessing a small integer. Found while testing the fix above.

  **Signing in with no company signed you out.** Authentication had worked
  perfectly; the missing thing was a company. Ending the session threw away the
  one part that succeeded and made it look like a rejected login — reported as
  "can't sign in", which is exactly how it felt. It now goes to sign-up, still
  signed in. Sign-up in turn stops offering a company to somebody who has one.

  **Removal told nobody.** It deleted the membership, the workspace assignments
  and the seat correctly, then said nothing, so the person's browser kept
  showing what it had loaded at sign-in. Narrowing access already sent a socket
  event; removing entirely did not, so the stronger act was the quieter one.

  **Email was never being delivered, for a reason no code change could fix, and
  it is fixed now.** Ten messages requested, ten processed, zero delivered, zero
  bounced, empty sending IP, nothing in any suppression list. Not the sender
  identity, not DNS, not credits, not reputation: the account itself was not
  releasing mail, which has no API and was a support matter. Osas raised it, the
  hold was lifted, and the same day closed with 12 delivered, 36 opens and a
  click. Worth recording that messages from the *unverified* sender delivered
  too once the hold went, so the from-address was never the blocker either.

  The lesson is the diagnosis, not the fault. SendGrid's own stats answered this
  in one call and nobody had looked; `requests` and `processed` climbing while
  `delivered` stays at zero, with an empty sending IP and empty suppression
  lists, is an account that is accepting mail and not sending it. No amount of
  reading application code would have found that.

  Two real faults were found on the way. The app sent from an address that was
  not a verified sender, now `noreply@syntextai.com`. And the SendGrid event
  webhook had been configured against an endpoint that did not exist, so every
  delivery event had been posting into a 405 since it was set up. That endpoint
  exists now (`/api/sendgrid/events`), which is what makes the next failure
  explain itself instead of being noticed weeks later.

- **2026-08-01 (why mail fails, and the end of the dependency triage):** Added
  `POST /api/sendgrid/events`, SendGrid's event webhook. A 202 from the send API
  means SendGrid accepted the message, not that anybody received it: rejection by
  the recipient's server, spam filing from an unauthenticated domain, and
  suppression from an old bounce all happen afterwards and silently, which is why
  invites could read as sent and never arrive. Failures now log with the
  `reason` field, which is the one that explains them. Public and unauthenticated
  by necessity, so it verifies the ECDSA signature when
  `SENDGRID_WEBHOOK_PUBLIC_KEY` is set, and always answers 2xx otherwise —
  SendGrid retries any non-2xx, and a batch holds up to a thousand events for
  unrelated messages, so one malformed entry must not cost the rest. Not stored
  in the database on purpose: the question is "why did this not arrive", the logs
  already have timestamps, and a table needs a migration, a retention policy and
  a screen before it beats grep.

  **The endpoint must be deployed before the webhook is configured in SendGrid**,
  or every event posts into a 404.

  **Dependency triage closed, 2026-08-01.** Backend `pip-audit`: clean, no known
  vulnerabilities. Frontend, two highs left and neither is actionable:

  - `react-router-dom` 7.18.2 — GHSA-qwww-vcr4-c8h2, RSC-mode CSRF. The advisory
    covers `7.12.0 - 8.2.0` and **no patched version exists in any line**; npm's
    suggested "fix" is a downgrade to 6.30.4, a major break across the whole
    routing layer. The vulnerable surface is React Router's RSC server runtime.
    This app imports `HashRouter`, `Route`, `Routes`, `Navigate`, `Link`,
    `useNavigate`, `useLocation`, `useSearchParams` and `useParams`, and nothing
    else — no server handler, no data-router actions or loaders, no RSC. Not
    reachable. Revisit when a patched 7.x or 8.x ships.
  - `vite` 5.4.21 — `server.fs.deny` bypass **on Windows alternate paths**, in
    the dev server. A devDependency that never ships, on a team running macOS and
    Linux. The fix is vite 8, three majors up, which is a deliberate build-tool
    upgrade rather than a security response.

  Both are recorded rather than suppressed, so the count staying non-zero is a
  known state and not an unread warning. What moved the number from 34 to 5 was
  the firebase 10 → 12 upgrade and the orphan-dependency removals, both earlier.

- **2026-08-01 (the free tier is gone, and two labels stopped lying):** Removed
  `FREE_DOC_LIMIT`, `FREE_STORAGE_LIMIT_BYTES`, `FREE_WORKSPACE_LIMIT` and every
  branch behind them, the upgrade banner and workspace-limit prompts,
  `useLimitHandler` (already unreferenced), and `UsageQuota`, which now exists as
  `SubscriptionNotice` and reports only a lapse. Reasoning in the pricing section
  above.

  **`list_accessible_workspaces` reported `role: "owner"` for admins**, because
  every caller wanted "may you manage this" and the field was named for something
  else. Two callers read the name literally and were wrong for it: the
  last-workspace guard counted an admin as owning workspaces they do not own and
  let them delete the organization's last one, and the free-plan count charged
  them for the owner's workspaces. Now `role` is what you are and `can_manage` is
  what you may do, and the guard asks whether the *organization* would be left
  with none, which is the invariant it was always about.

  `can_manage` then vanished on the way out, because `WorkspaceResponse` did not
  declare it and `response_model` silently drops undeclared fields. The Team
  button disappeared for the owner of the company. Worth remembering: a field
  added to a route's dict is not a field the browser receives.

  **The app looped between two organizations.** `fetchSubscriptionStatus`
  refreshed the org context using the `activeOrganizationId` captured when the
  callback was built, which right after a switch is the *previous* organization.
  That alone flips once. It ran forever because the Firebase auth subscription
  listed callbacks in its dependencies that are rebuilt whenever the active
  organization changes, so every change tore the listener down and re-subscribed
  it, and re-subscribing fires immediately. 245 registrations and 412 context
  loads in one sitting, and it paused when the tab was backgrounded because
  browsers throttle it there. Invisible until an account owned one company while
  belonging to another: with one organization, the stale id was the right id.

  Killing that loop removed an accidental refresh it was doing, which is what
  produced the free-plan banner on a paid account. Entitlement is now re-fetched
  deliberately when the active organization changes, through a ref, so the
  refresh happens without the identity churn.

- **2026-07-31 → 2026-08-01 (storage moved to the tenant):** Objects were keyed
  by the uploader's firebase uid while access, retrieval and citations were all
  keyed by workspace. That one mismatch is why deleting a person deleted
  documents belonging to a company they had merely joined, and why the worker
  reversed an uploader's uid out of a URL to fetch bytes it already had the id
  for. Details in "As built" above. Also fixed in passing: deleting a workspace
  never removed its objects, deleting a document authorized by uploader so an
  owner could not remove an admin's file (and a file whose uploader was deleted
  carried `user_id NULL` and could never be deleted by anyone), and moving a
  document between workspaces left the object under the old prefix, so deleting
  the old workspace would have destroyed a document belonging to the new one.

- **2026-07-29 (multi-user actually works now):** Invited staff could join a
  workspace and then **do nothing in it**. Both read paths filtered on
  `files.user_id` with `workspace_id` only ANDed on top, and documents belong to
  whoever uploaded them, normally the owner. So a staff member listed zero
  documents, retrieved zero chunks, and got no answers at all. Collaboration was
  not missing polish, it did not function. Visibility now follows the workspace
  via `accessible_workspace_ids()`, with `check_can_read_workspace` authorizing
  any requested workspace, since `workspace_id` alone now scopes reads.

  **Two authorization holes closed.** `update_workspace` and `delete_workspace`
  both said owner-only in their docstrings, then checked
  `list_workspaces_for_user`, which returns workspaces the caller merely belongs
  to. Any invited staff member could rename and **permanently delete** the
  owner's workspace and every document in it. The role was in that response and
  simply unused. The delete route's "cannot delete your last workspace" guard
  also counted memberships, so one owned plus one joined looked like two.

  Also fixed: staff were pushed through trial signup because entitlement was
  per-user. `resolve_entitlement` now follows workspace membership to the owner's
  plan for app access, and `assert_can_create_doc` resolves limits against the
  workspace's billing owner with usage counted per workspace. Note the
  deliberate asymmetry: app access is a global question, but quota and the
  workspace-creation limit stay keyed to the user's own subscription, because a
  person can be premium in someone else's org and free in their own.

  Verified against the local stack: staff see 1 document where they saw 0, an
  outsider sees 0 and resolves no accessible workspaces, reads allow owner and
  staff and 403 outsiders, uploads stay owner-only, rename/delete admits the
  owner and blocks staff.

  **This is interim.** The correct fix is the tenant model above.

- **2026-07-29 (worker concurrency, stage timing, dead-code sweep):** The worker
  used one global semaphore for every run type, so with the production
  `MAX_CONCURRENT_TASKS=1` a **chat question queued behind a document upload**.
  One tenant uploading a large PDF stopped every other tenant from getting an
  answer. Queries and ingests now have separate budgets (`QUERY_CONCURRENCY=4`,
  `INGEST_CONCURRENCY=2`), and a file at or above `HEAVY_FILE_BYTES` (8MB) takes
  an exclusive slot so two large documents never overlap.

  The loop also had a **batch barrier** that defeated the priority ordering
  entirely: it claimed a batch then waited on `ALL_COMPLETED` before polling
  again, so the batch moved at the speed of its slowest member and nothing new
  was claimed meanwhile. Queries are enqueued at `priority=10` against `200` for
  ingests, but a query arriving a second after a big PDF was not *looked at*
  until that PDF finished. The loop now wakes on `FIRST_COMPLETED` and refills
  the freed slot immediately.

  Also added: a per-tenant cap (`MAX_RUNS_PER_USER=3`) so one user queueing
  twenty files cannot starve others, and **lease reclaim** —
  `lease_expires_at` was written but never read, so a worker killed mid-job left
  runs stuck in `running` forever, never retried, never surfaced, with the user's
  upload silently stalled.

  On the old "sequential processing prevents OOM" rationale: it was stricter
  than necessary. `PDFProcessor` already batches 50 pages, so peak memory per
  job is bounded by the batch, not the document size. The heavy slot is the
  belt-and-braces guard. **Watch worker RSS against the TIMING logs before
  raising `INGEST_CONCURRENCY` further**; the container is capped at 2GB.

  The worker also had a permanently-failing healthcheck: it shares an image
  whose `HEALTHCHECK` curls port 3000, which the worker never serves. It was
  always red, so real stalls were invisible. The loop now writes a heartbeat and
  the compose healthcheck asserts freshness.

  **Verified against the local stack**, not just reasoned about: expired leases
  reclaimed and requeued, per-user cap held (claimed 3, deferred 3), and
  in-flight top-up observed at 2/6 while jobs were still running, which is the
  batch barrier being gone. All three containers healthy.

  New `api/core/timing.py` emits structured `TIMING` log lines per stage: queue
  wait (upload or question until work actually starts), download,
  extract/embed/store, and query latency with chunk counts. Log-based rather
  than PostHog so it survives analytics failures and behaves identically in the
  worker, which has no request context. Read them with:

  ```bash
  docker logs syntextai-worker-local 2>&1 | grep TIMING
  ```

  Dead code: AST reachability analysis from the four externally-called entry
  points in `llm_service.py` found **13 unreachable functions, 744 lines** — the
  entire key-concepts and MCQ cluster. Removed, along with four now-unused
  imports, the dead `generating_concepts` status in `files.py`, and its three
  frontend handlers. Note the earlier orphan audit missed all of this because it
  worked at file level ("is this file imported?") and `llm_service.py` obviously
  is; vulture does not flag unused module-level functions at default confidence.
  **For function-level dead code, do the reachability pass, not a grep.**

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
