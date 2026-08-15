# SyntextAI — Engineering Overview

Orientation, current state, and the decisions worth not making twice.

**This is not a changelog.** Git holds what changed and why, in commit messages
written for that purpose, and the code holds the reasoning in comments beside
the thing being reasoned about. What belongs here is what neither of those
surface when you need it: how the parts fit together, what is shipped, what is
next, and what was deliberately not built.

Keep it lean. If an entry could be a commit message or a code comment, it
should be one.

## What SyntextAI is

Internal documents, made instantly answerable by a whole team, with every
answer cited back to the page it came from.

**Customers:** small businesses, 10 to 100 people, in document-heavy work:
dental, accounting, legal, property management, trades, insurance,
manufacturing. Live and taking money since 2026-08-03.

**What it actually is, structurally:** an agent harness scoped to one company at
a time, currently shipping with one tool, which is "know things about your
documents". That is why it looks like document Q&A from outside.

The hard part is already done, and was done by being careful rather than by
plan. An agent loop is a weekend; multi-tenancy touches every query and cannot
be retrofitted. Hermes Agent's own issue reads *"one agent = one tenant. Memory
is global, sessions don't scope by tenant, and there is no isolation between
groups, channels, or users."* That sentence describes what this codebase has
made impossible, and it is the thing to sell.

### Four layers, so an idea sorts itself

| Layer | What it is | Decides anything? |
|---|---|---|
| **Sources** | Ways knowledge gets in | No |
| **Surfaces** | Places to ask | No |
| **Tools** | Things the agent chooses to do | Yes |
| **Memory** | Standing facts that are in no document | No, but changes every answer |

Memory does not exist yet and is the most valuable missing piece. Customers
already offer it through feedback: *"cited the 2019 policy, we are on the 2024
one"* is a standing fact being handed over and thrown away.

### The test for anything new

**Can the person who benefits say yes by themselves?** If yes, build it. If
somebody else has to approve it first, park it until a customer asks by name.

That one question sorted a week of decisions: it kept email and Drive, and
parked SharePoint, Outlook, Slack and Teams.

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

Sixteen tables, all of them in `api/models/orm_models.py`. Verified against the
live schema 2026-08-12.

- **Organization / OrganizationMember** — the tenant. A company owns workspaces
  and pays the subscription. A member has a role (owner, admin, staff) and a
  reach (the whole organization, or named workspaces).
- **Workspace / WorkspaceMember / WorkspaceInvite** — a set of documents and the
  people who can see them. Invites are UUID tokens, 7 day expiry, single use.
- **File / Segment / Chunk** — a document, its pages, and its retrieval units.
  A citation names a page, so the page is what a reader opens; chunks are what
  retrieval matches. `chunks.content_hash` is what stops the same text being
  embedded twice.
- **ChatHistory / Message / MessageFeedback** — a conversation, its messages,
  and what somebody thought of an answer.
- **AgentRun** — the job queue. Also the record of what each query did, which is
  what a rating is read against.
- **Subscription / CardDetails** — Stripe state, keyed to the organization.

**Multi-tenancy is enforced in the application, not the database.** There is no
Postgres RLS here, so every query touching a document, conversation or rating
must reach its organization explicitly. Those tables carry no organization of
their own: they get there by joining through the workspace, and that join *is*
the tenant boundary. Leaving it off is how one company sees another's data, and
nothing underneath will catch it.

Two questions, two functions, and nothing else. *May you see it* is
`accessible_workspace_ids`. *May you do it* is `assert_workspace_capability` or
`assert_organization_capability`. Every access bug in this codebase has been
answering one of those some other way.

## How a request actually flows

Rewritten 2026-08-07. The previous version described a RAGFactory, a
HybridSearchEngine and a reranker, none of which exist any more. Read this one
against the code before trusting it; a stale flow diagram is worse than none,
because it is believed.

### Ingesting a document, once

```
upload, or import from Drive -> GCS      either way it is an ordinary file row
       -> queued as an ingest run        the worker decides when, and for whom
       -> fitz: text per page
          PDF pages the text layer probably lost also go to the vision model
          DOCX tables are rendered as markdown; .md keeps its own structure
       -> chunk_markdown where the page has structure, chunk_text otherwise
          both 400 tokens, 20% overlap; a table is cut on rows, never mid-row
       -> hash each chunk; embed only what this organization has not embedded before
       -> ONE segment per page      the citation unit, a page is what a reader opens
          N chunks beneath it       the retrieval unit, what gets embedded and searched
       -> written batch by batch, 50 pages at a time, each batch its own transaction
       -> zero chunks means the file is marked FAILED, not processed
```

**Ingestion makes exactly two kinds of model call, and neither is generation.**
Embeddings, once per batch of new chunks, which is what makes a document
findable at all; and the vision model, PDFs only and only for pages the text
layer probably failed. No chat call happens anywhere in this path.

It used to. A contextualiser wrote a sentence per chunk describing what the
passage was about, one LLM call per chunk plus one per document, so a 500-page
manual bought roughly 1,500 calls at ingest. Measured 2026-08-15 it retrieved
nothing extra and ranked slightly worse, and it destroyed the embedding-reuse
saving as a side effect, so it was deleted rather than parked behind a flag. See
"Contextual retrieval, measured properly and then removed".

Two consequences of writing per batch. A crash keeps the pages it reached and
resumes from the first unstored page, because a page either has its segment or
has nothing. And a long document is searchable while it is still ingesting,
since retrieval scopes by workspace and never looks at processing status.

`chunks.segment_id` is what ties a retrieved chunk back to the page it is cited
as.

**Documents ingested before 2026-08-07 have a null `chunks.content`, and there
is no fallback.** This section used to claim retrieval falls back to the
segment's text for them; checked 2026-08-15, nothing does. `hybrid_search`
selects `COALESCE(c.content, '')` and no later stage substitutes the page. Both
keyword arms rank `chunks.tsv`, which is generated from that same null column,
so those chunks cannot be matched by words at all, and they cannot be
re-embedded either, because the text they were made from is stored nowhere. They
are reachable only by a vector nothing can rewrite. The only fix is uploading
the document again. `reembed_chunks --check` counts them per workspace.

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

## Shipped

Everything below is live in production. Read the commit if you need the why.

**The product**
- Ask a question, get an answer cited to a page. PDF, DOCX, TXT, MD.
- **Find**: search returning the pages that matched, in ~0.6s, no generation.
- Workspaces inside organizations, invites, roles, per-workspace document access.
- Stripe: trial, subscribe, plan changes, 3D Secure, card update.
- Answer feedback: thumbs, four reasons, a comment, joined to the run that produced it.
- **Usage** in Settings: questions asked, who is asking, documents including the
  failed ones, what the team thought, which documents nothing has ever used.
- **Import from Google Drive** through the picker, `drive.file` only.

**Underneath**
- Durable job queue in Postgres: leases, retries, per-tenant fairness, separate
  query and ingest budgets.
- Redis as a notification bus. Work is announced, so a queued job starts in
  ~0.2s instead of waiting up to 10s to be noticed. Postgres remains the record.
- Short-lived answer cache, invalidated the moment a workspace's documents change.
- Ingestion writes each batch as it goes: a crash keeps the pages it reached and
  a document is searchable while it finishes.
- Text is embedded once per organization; re-uploads and repeats cost nothing.
- Security: CORS locked, rate limits on paid endpoints, security headers,
  no exception text returned to clients, signed URLs for documents.

## Next

In order. The first two need nothing from anybody.

1. **Email in.** Forward a document to an address and it is in the knowledge
   base. No OAuth, no admin, no app to install. SendGrid Inbound Parse is the
   same shape as the signed webhook `sendgrid_events.py` already receives.
2. **Vision extraction.** Built on `develop`, unmerged, and **not yet proven**.
   See the section below: the feature works, the speed does not, and there is
   no benchmark number for it yet.
3. **Memory.** Standing facts per organization and workspace, editable, fed by
   the feedback that already arrives.
4. **The agent tool layer.** ~~Built and behind `AGENT_MODE=tools`~~ **This is
   stale, corrected 2026-08-15.** There is no `AGENT_MODE` in the codebase and
   no tool layer. `tool_agent.py` and `document_tools.py` were deleted in
   40ca829, "One pipeline that sometimes loops, instead of a pipeline and an
   agent", after the two systems scored 16.2 and 17.0 calling the same
   `hybrid_search` and three regressions came from the code around them
   drifting. What survives is `query_agent.py`, one graph that retrieves again
   when coverage says a question has a second information need. Rebuilding a
   tool layer is a fresh decision, not a flag to flip.

## Vision extraction: in progress, not merged

On `develop` as of 2026-08-13. Written down here because most of what has been
learned is negative, and a negative result is only worth anything if the reason
is recorded.

**The problem it exists for.** `page.get_text` returns characters in PDF storage
order, so a table arrives as a column of loose values with every row destroyed.
Measured on a 433-page corpus of HVAC service manuals (`api/evals/hvac_benchmark.yaml`):

| group | overall | citations |
|---|---|---|
| error codes | 3/5 | 3/5 |
| charging chart | 0/3 | 1/3 |
| physical data | 2/4 | 2/4 |
| figures | 0/4 | 1/4 |
| prose | 3/4 | 4/4 |
| **total** | **8/20** | **11/20** |

Prose works. Tables are a coin flip. Figures are zero. The sharpest single
result is question 6: the **right page was cited** and the answer still said 78
where the page reads 74, because the row that value belongs to no longer existed
by the time the model saw it.

**What is built.** A page goes to `llama-4-maverick` only when the text layer has
probably failed it (digit density, image area, or empty). Every number the model
returns is checked against the text layer; a page that introduces more than five
numbers absent from the page is rejected outright. Proven on the charging chart:
rows come back intact, including the 74 and 94 that questions 6 and 7 get wrong.

**What is not proven: whether any of that moves 8/20.** No measurement exists.

### The speed problem, and three wrong explanations

A page takes 120 to 180 seconds. 58 of the Carrier manual's 69 pages qualify, so
one 69-page document is about an hour. Three explanations were proposed and
measured, and the first two were wrong:

1. *"Routing is too loose."* No. Page-by-page digit density on the Carrier
   manual: 58 pages qualify at the 0.12 threshold and **51 still qualify at
   0.30**. It is genuinely a book of tables. Corpus-wide the rule picks 112 of
   433, about a quarter. The threshold is fine.
2. *"The model is writing too much."* No. Measured output is **490 to 700
   tokens** per page, taking 122 to 169 seconds. That is 3 to 6 tokens/second
   against a normal 25 to 60, so almost none of the time is generation.
3. *"It is image prefill, so lower the DPI."* **Unproven, and the first attempt
   to measure it was invalid.** A descending sweep (150, 110, 80, 60) had the
   150 run succeed at 177s and every subsequent run fail: ReadTimeout, 408,
   RemoteProtocolError. That is indistinguishable from the endpoint degrading
   under repeated calls, so it says nothing about DPI. Any re-run must
   interleave settings and repeat each one.

The endpoint's behaviour under load is now the open question, and it may also
explain the earlier finding that `VISION_CONCURRENCY=8` produced timeouts.

### Resolved: moved to DeepInfra 2026-08-13, and it was the provider

Same model weights, same prompts, same pages:

| | DigitalOcean | DeepInfra |
|---|---|---|
| vision, page 9 (charging chart) | 165s | **16.5s** |
| vision, page 62 (physical data) | 148-186s | **12.3s** |
| chat, time to first token | 23.8s / 26.7s | 2.1s total |
| embeddings | Voyage, 11-18ms/chunk | Qwen3-0.6B, 0.2s, 1024-dim |

All hand-verified values survived on both pages, including the 74 that benchmark
question 6 gets wrong. The Carrier manual goes from 2.7 hours of extraction to
about five minutes.

**The lesson worth keeping.** Four explanations were proposed and measured for a
165-second page — pages routed, output length, image size, request queueing —
and every one was about what we send. All four were wrong. The check that found
it in five minutes was "is this speed normal for this model", against a public
throughput figure. Ask that first next time.

**What did not just work: reasoning models spend the answer's budget thinking.**
`gpt-oss-20b` returned 1,394 characters of `reasoning_content`,
`finish_reason=length`, and `content: ""` — a successful HTTP 200 with no answer.
`_post_json`'s `accept()` already existed for this, so it is not new, but it is
intermittent, which surfaces as an answer that is occasionally blank rather than
as something obviously broken.

**This is a hazard for the next short call somebody writes, not a live defect.**
Read it before adding any helper that asks the model for one line.

Thinking and answer come out of the same `max_tokens`, and thinking length is
set by the task rather than by the budget. So a *smaller* request is a *more*
dangerous one: ask for 150 tokens because that is what a sentence costs and the
deliberation eats all of it. The chunk contextualiser asked for 200 and had 308
of 316 chunks come back empty.

Three defences, all in `llm_service.py`:

- `MIN_COMPLETION_TOKENS = 500`, applied inside `gradient_chat` so a caller
  cannot undercut it. It took the contextualiser from 97% empty to 14%.
- `_has_content` is passed to `_post_json` as `accept`, so an empty body is a
  failed request and is retried three times. That 14% was *after* the retries,
  so this is not random: some prompts reliably send the model into long thinking.
- `reasoning_effort` per call, because the setting is shared. Raising the answer
  path from low to medium on 2026-08-14 was right and bought four citations, and
  it silently broke query expansion, which began returning `""`, then `[]`, and
  retrieval quietly lost an arm with no error anywhere.

Current exposure, checked 2026-08-15: two callers of `gradient_chat`.
`query_processor.prompt_llm` passes `low` explicitly; `generate_explanation`
asks for 1,500 tokens. Neither is at risk. Ingestion makes no generation calls
at all.

The reason both incidents stayed invisible is that `""` means both "the model
said nothing" and "the model failed to answer". If this bites a third time, make
an empty completion loud at the boundary rather than letting each caller inherit
silence.

**Model ids are matched exactly and are namespaced with a slash.** The live
config had a trailing space on `MODEL_CHAT_ID`, which DigitalOcean tolerated. A
wrong id returns "model not found", which reads like an outage rather than a
typo.

### The measurements that led there, 2026-08-13

Every speed hypothesis was measured and every one was wrong, because they were
all about *what we send*. The cause is *where we send it*.

`llama-4-maverick`, the model we already run, has a published median of **123
output tokens/second** across providers; Azure serves the same weights at 368.
**We measure 3.** Not a slow model: an ordinary model on a provider serving it
roughly forty times slower than anybody else.

Costed on our own numbers — the Carrier manual, 69 pages, 58 to vision, ~412k
input and ~35k output tokens:

| option | speed | this document | cost/document |
|---|---|---|---|
| DigitalOcean (today) | 3 t/s | **2.7 hours** | ~$0.14 |
| same model, DeepInfra | 98 t/s | ~6 min serial | $0.11 |
| same model, Azure FP8 | 368 t/s | **~3 min serial** | $0.12 |
| same model, Bedrock | 230 t/s | ~4 min | $0.13 |
| Mistral OCR 4 | purpose-built | seconds | $0.28, $0.14 batched |

**Cost is not the variable.** Everything lands between 11 and 28 cents a
document. The variable is a factor of fifty in time, paid for no saving.

`VISION_BASE_URL` and `VISION_API_KEY` now exist so vision can move providers
without touching chat, which is a different model with different behaviour.
Moving is two environment variables.

Mistral OCR 4 is the strategic option rather than the quick one: $4/1,000 pages,
86.2% on table extraction (ahead of GPT-5.4 Pro, Claude Opus 4.6 and Gemini
3.1), and it returns **block types, bounding boxes and per-block confidence**,
which is precisely what `chunk_markdown` wants as input rather than having to
infer from pipes. Its documented weakness is merged cells and nested headers,
which is exactly what a charging chart is, so it gets measured against page 9's
48 hand-verified cells before it ships. Those benchmark numbers are somebody
else's measurement.

**Do not tune anything else against the current endpoint.** The same prompt on
the same page returned byte-identical output in 188s and 113s, a 66% spread.
That is wider than any prompt or DPI effect worth having, so no A/B run here can
resolve anything. Two prompt comparisons were abandoned for this reason.

### Three bugs this work exposed, all fixed 2026-08-13

None were introduced by vision. All three were latent and invisible while
extraction took milliseconds.

- **A running job did not hold its lease.** `lease_expires_at` meant "started
  less than 15 minutes ago", not "somebody is holding this". An hour-long
  extraction was reclaimed at 15, 30 and 45 minutes and then failed as "worker
  presumed dead" while the worker was alive and working. Each reclaim started a
  **second concurrent extraction of the same document** against a metered
  endpoint. `_hold_lease` now renews while the job runs, conditional on the row
  still being ours.
- **Extraction was not resumable.** Item 18 made *storage* resumable, but all
  extraction completes before the first batch is written, so a crash discarded
  every page already paid for. Roughly three hours of vision calls bought
  nothing on 2026-08-12. `page_reads` now stores each page as it lands.
- **One refused page killed the whole document.** `asyncio.gather` without
  `return_exceptions` returns the instant one page raises, with its siblings
  still in flight, and the bare exception fell through to the handler that
  returns `[]`. Found by the cache test, which expected pages 1 and 2 on disk
  and found only page 1.

## Parked, and why

Nothing here is abandoned. Each is one customer request away.

| Parked | Why | What exists already |
|---|---|---|
| SharePoint, Outlook | Need a tenant admin to consent | Working SharePoint adapter, tested |
| Slack, Teams | Need an admin, and SMBs mostly are not there | Signature verification, 11 tests |
| Computer-use on portals | Hardest thing on the list, and credentials are heavy | Nothing |
| Self-hosting models | A sales decision, not a cost one, at this size | Nothing |
| Manufacturing-specific ingestion | Shelved with that pivot | Nothing |

**Computer-use is parked, not dismissed.** An earlier note called it enterprise
automation with no nameable job and had the argument backwards: enterprise
software has APIs, small-business software does not. Dentrix is a Windows
application and insurance eligibility lives on portals with no API. The job is
nameable, which is the test: the front-desk person checking eligibility on a
payer portal every morning. When it is built: read-only, human-triggered,
results returning as citable knowledge, and portal credentials treated as the
heaviest thing we would ever hold.

## Consolidating onto one inference provider, decided 2026-08-13

Three providers today: DigitalOcean (chat, vision), Voyage (embeddings), and
that is one bill and one status page too many. Moving to DeepInfra. The reasons
differ per piece, and two of the three are not what they look like.

**Chat and vision: moving because the current provider is broken.** Measured:

| call | DigitalOcean measured | DeepInfra published |
|---|---|---|
| vision, one page | 165s, ~3 tok/s | ~6.6s, 98 tok/s |
| chat, first token | **23.8s / 26.7s** | ~0.5s |
| chat, generation | 3.9-4.5 tok/s | 98 tok/s |

24 seconds before a customer sees the first word of an answer. That is the real
reason the app feels slow, not the absence of streaming. `openai/gpt-oss-20b` is
the identical model on DeepInfra ($0.03/$0.14 per M), so chat is a URL change
with nothing stored to migrate.

**Embeddings: NOT moving for speed or money, and this needs saying plainly so
nobody "optimises" it later on a false premise.** Measured 2026-08-13,
voyage-3.5-lite: 11-18ms per chunk at batch 128, ~2s for a 100-chunk document.
It is nowhere near a bottleneck. Price is $0.02/M against Qwen3's $0.01/M, and
the entire local corpus (1,833 chunks, ~733k tokens) is **1.5 cents against 0.7
cents.**

The actual reason is lock-in. `voyage-3.5-lite` is proprietary and single-source:
only Voyage serves it, so every vector ever stored depends on one company's
pricing and roadmap, and leaving means re-embedding everything under duress.
`Qwen3-Embedding-0.6B` is open weights served by many providers and self-hostable
— same weights, same vectors, so a future provider change costs nothing. It is
also 1024 dimensions, matching the existing pgvector column.

**Embeddings are a data migration, not a config change.** A Voyage vector and a
Qwen vector for identical text are unrelated numbers. Embedding queries with one
model while documents hold the other does not error; it silently returns
confident citations to the wrong pages, which is the worst failure this product
has. So: second column, backfill, switch reads, and the citation benchmark as
the gate. If Qwen retrieves worse, keep Voyage and we are still down to two
providers.

Order: vision (no stored data), chat (no stored data), embeddings (its own
migration, its own measurement). Doing them in one change would make a retrieval
regression impossible to attribute.

## Ingestion, 2026-08-15: what DOCX and TXT documents were losing

Two faults, both only in the Word and text paths, both invisible to every
measurement taken so far because the benchmark corpus is PDFs. Found by reading
the ingestion code rather than by a failing question.

**Punctuation was deleted before embedding.** `clean_text` had a `csv_like`
branch that stripped everything except letters, digits, commas, dots, spaces and
newlines. `detect_content_type` chose it for any text with a comma and more than
three lines, which is ordinary prose. It could never fire on a PDF, because a
PDF page carries its "Page N" marker and matched an earlier branch first, so the
damage fell entirely on the formats an SMB customer actually uploads.

Driven through the real ingestion path against the local database, one Word
policy document, before and after:

| in the document | stored in `chunks.content` before | after |
|---|---|---|
| `billing@acme.com` | `billingacme.com` | unchanged |
| `555-0134` | `5550134` | unchanged |
| `1.5%` | `1.5` | unchanged |
| `$1,200/month` | `1,200month` | unchanged |
| `(see Schedule A)` | `see Schedule A` | unchanged |

That column is not only what a reader sees. It is what gets embedded, what the
keyword index is built from, and what the model is handed to answer from. It
also disarmed the literal-token arm of the search, whose whole job is to let a
rare token decide the ranking: nobody searches for `5550134`.

The branch is gone rather than fixed. Real CSV uploads are not supported, and if
they become supported they need a parser, not a regex deleting punctuation from
every other format on the way past.

**A Word table was not a table.** `_chunk_table` cuts on row boundaries and
repeats the header above every piece, and only PDF pages read by the vision
model were ever marked `is_markdown`, so no DOCX ever reached it. Rows were
joined with a bare `" | "`, with no pipes on the ends and no separator row, so
they would not have been recognised as a table even if the flag had been set.
A 60-row pricing table went through the general 400-token splitter: cut mid-row,
header left behind in the first piece. Same failure as the charging chart on
2026-08-13, one format over.

Tables are now rendered as real markdown and marked as such. Same document,
same real path: 3 table chunks, every one carrying the `Table 1` caption and the
`| Plan | Users | Monthly | Annual |` header, every row intact. Before: 0 chunks
matched a table shape at all. `.md` uploads are marked from their extension too.

**Nothing needed re-ingesting.** Both faults are written into stored chunks, so
an affected document stays damaged until it is uploaded again. Osas confirmed
2026-08-15 that no DOCX or TXT document exists in production yet, so there was
nothing to backfill. If that ever stops being true before this ships somewhere
new, the rule stands: a number measured on re-processed data does not describe
what is in production.

**The benchmark still cannot see any of this.** `citation_benchmark.yaml` is
PDFs. Until it has DOCX and TXT questions in it, this whole section is verified
by driving the pipeline and reading the stored rows, which is how it was done,
and not by a score.

## Three things written and read by nothing, resolved 2026-08-15

Found by reading the ingestion path. Each was written on purpose and then lost
its reader, which is the state that quietly rots: a future reader cannot tell
whether the column is authoritative, and `segments.tsv` was a GIN index
maintained on every insert to answer a query nobody sends.

**The vision verification flags: finished.** `_read_page_with_vision` decides,
deliberately, to keep a figure page whose numbers the text layer cannot confirm
and to flag it instead of rejecting it. Enforcing the strict rule there cost
four benchmark questions on 2026-08-14, so the decision matters. The flags went
into `page_reads`, which is an extraction cache nothing queries while answering,
and the in-run collection was assigned twice and read nowhere. They now travel
on the page to `segments.meta_data`, which `hybrid_search` already returns with
every result. Displaying them to a reader is still an open design decision.

**`files.outline`: deleted.** Not an untried idea, a tried one. It had two
consumers and both are gone: the `outline` tool, whose own comment on
2026-08-05 read "The `outline` tool exists and the model does not call it,
exactly as it did not call read_page when told to. Two prompt revisions failed
to change that", and then `workspace_map`, which stopped asking and stuffed
every document's contents into the prompt, which went with the whole tool layer
in 40ca829. Extraction also cost a full `get_text("dict")` pass over every page
of any PDF without an embedded contents page, paid on every upload for nobody.
`api/services/outline.py` and `api/scripts/backfill_outline.py` are in the
history at c026c2d if the idea comes back with a consumer attached.

**`segments.context` and `segments.tsv`: deleted.** The context sentence went
entirely, see the section below. `segments.tsv` had not been queried by anything
since chunk-level retrieval landed on 2026-08-06, and was a GIN index maintained
on every insert to answer nothing. The segment keeps `content`, which is the
page a citation opens, and that is what a segment is for. Retrieval measured
before and after: 17/17 and 8/10, unchanged.

Also gone: the vestigial `segments` join in the text arm of `hybrid_search`,
left from when that arm read `s.tsv`. It ranked `c.tsv` anyway and every chunk
has a segment, checked across 2,633 rows, so it decided nothing and only cost
work.

## The embedding model changed and the documents did not, 2026-08-15

Confined to the development database. **Production is clean, confirmed by Osas
2026-08-15**, so no customer was ever answered from this. What it did cost is
every local retrieval measurement taken between the provider switch on 08-13 and
the repair on 08-15, which includes the runs that rejected contextual retrieval.

Worth writing down anyway, because it is the failure the provider consolidation
section above predicts in writing, including the word "silently", and because
nothing in the system noticed for two days.

**What happened.** Reads moved from `voyage-3.5-lite` to
`Qwen3-Embedding-0.6B`. Both are 1024 dimensions, so every stored vector still
fits the column, every query still returns rows, and nothing anywhere raises.
Questions were being embedded with one model and compared against documents
holding another, which is arithmetic on two unrelated coordinate systems.

**How it was found, and the shape of the check worth keeping.** Embedding is a
pure function of its input, so a chunk's stored vector and a fresh embedding of
that chunk's own text must be the same vector. Cosine near 1 means the same
model wrote it. Near 0 means a different one did. One embedding call per
workspace answers it, and it needs no benchmark and no opinion:

| workspace | chunks | uploaded | cosine |
|---|---|---|---|
| 1 | 184 | 2026-08-08 to 08-12 | -0.044 |
| 4060 | 210 | 2026-08-06 to 08-07 | -0.044 |
| 4219 | 540 | 2026-08-06 | -0.053 |
| 7354 | 1607 | 2026-08-12 | **+0.9998** |

**What it cost, measured on the SMB benchmark corpus.** `retrieval_ranks.py`,
which asks only whether each question's required page reached the fused top 25
and at what rank. No model in the loop, so the numbers are exact rather than a
draw from a distribution:

| | before repair | after repair |
|---|---|---|
| single-document questions with their source retrieved | **6/17** | **17/17** |
| multi-document questions with every source retrieved | **0/10** | **8/10** |

Eleven of seventeen questions could not retrieve their own answer. The documents
were fine, the chunking was fine, the index was fine, and the product was
answering from whatever happened to land nearest in a space that meant nothing.

**The repair.** `api/scripts/reembed_chunks.py`. `--check` audits every
workspace with one call each; `--workspace N` or `--all` re-embeds with the
model configured now and rewrites `content_hash` to match, because the hash is
the sha256 of the text that was actually embedded and a stale hash beside a new
vector poisons the reuse cache for every document sharing that text.

**Production was checked and is clean**, with the tool rather than by argument:
`--check` on the live box after the 2026-08-15 deploy returned every workspace
above +0.95 and "0 workspace(s) need re-embedding". That same result settles a
second question, which is why it is worth running after any removal and not only
after a provider change: a corpus embedded from context plus text could not
match a fresh embedding of the text alone, so it also proves
`CONTEXTUALIZE_CHUNKS` was never on in production and that deleting the
contextualiser changed nothing for any stored document.

Run it again after any future change of embedding model or provider. This
failure presents as a customer saying the answers got worse, never as an error
anybody sees.

**122 chunks could not be repaired.** They pre-date `chunks.content` (added
2026-08-06), so the text they were made from is stored nowhere and they are
invisible to both keyword arms as well. The only fix is uploading those
documents again. The check reports them rather than quietly re-embedding 88 of
210 and calling it done.

**The lesson, and it is the same one as the 165-second page.** The overview
already said embeddings are a data migration and named the gate: second column,
backfill, switch reads, benchmark. In production that was followed. Locally the
reads were switched and the documents were not, and nothing anywhere said so for
two days, which is the part that would have mattered had it gone the other way.

**The narrower lesson, which cost real work here.** A benchmark run says nothing
about retrieval unless the corpus it ran on is in a state somebody has checked.
Three separate conclusions were drawn from this corpus while it was broken. Run
`--check` before trusting a local retrieval number, not after being surprised by
one.

## Contextual retrieval, measured properly and then removed, 2026-08-15

Deleted: `services/contextualizer.py`, `scripts/backfill_context.py`,
`CONTEXTUALIZE_CHUNKS`, `chunks.context`, `segments.context`, and the weighted
tsvector. Embedding input is now the chunk's text and nothing else.

**Why the old number meant nothing.** The 2026-08-14 result could not have said
what it claimed: the keyword half was never wired up, the backfill flattened
every chunk of a page to one page-level vector, and the corpus held vectors from
the wrong model. Three reasons for a bad number, none of them "context does not
help".

**So it was measured once, properly.** Mechanism complete, corpus repaired, 463
of 540 chunks contextualised (77 calls came back empty, the reasoning-budget
failure described above):

| | no context | with context |
|---|---|---|
| single-document retrieved | 17/17 | 17/17 |
| multi-document retrieved | 8/10 | 8/10 |
| mean worst rank | **5.80** | **6.12** |
| worst rank improved / unchanged / degraded | | **4 / 14 / 7** |

Nothing new retrieved, ranks slightly worse.

**Why it was never likely to work here, which is the part worth carrying.**
Anthropic send the entire document with every chunk and rely on prompt caching
to afford it. With no prompt caching this sent one document-level précis shared
by every chunk of that document, which is a different technique wearing the same
name. The context it produced said "the heat-pump service manual", true of all
five manuals and near-identical for every chunk of a page, so at weight 'A' it
crowded out the body text while adding no way to tell anything apart.

Underneath that is a property of the corpus, and it is the same one that beat
the cross-encoder: contextual retrieval pays when a passage is ambiguous alone
and the document identity resolves it. Five near-identical service manuals, or
five small-business government guides, are exactly where document identity
resolves nothing. It also costs the embedding-reuse saving, because a per-chunk
context sentence makes the embedded text document-specific, so even a neutral
result was a net loss.

**The test to apply next time** is not "has somebody published good numbers for
this". It is "is there a reason this mechanism separates OUR documents from each
other". Both rejected retrieval ideas failed that question and neither was asked
it first.

If it is ever retried, on a genuinely mixed workspace and with per-chunk context
rather than a shared précis, the numbers above are what it has to beat, and the
empty-response failure has to be fixed first.

## Retrieval, 2026-08-14: four ideas measured, four rejected

The benchmark answers the same question the same way now, so a one-question
difference is a fact rather than a coin flip. That is what made this possible
and it is the only thing here that shipped.

**Answering temperature is 0.** It was 0.1. Measured 2026-08-13, three
consecutive runs of the same questions over the same corpus: 6, 7 and 8 out of
20. Two questions flipped with nothing changed but the dice. That is not
benchmark noise to average away, it is the product being non-deterministic about
facts, in a tool that reads torque values off service manuals.

It also means **every number quoted before this is suspect**, including the
"8/20 baseline". That was one draw from a distribution centred near 7.

### The four, on one corpus, all repeatable to the question

| config | overall | citations |
|---|---|---|
| **untouched pipeline** | **8/20** | **9/20** |
| truncate candidates to 15 | 7/20 | 9/20 |
| cross-encoder rerank + truncate 15 | 6/20 | 8/20 |
| cross-encoder rerank, no truncate | 7/20 | 9/20 |
| model-written chunk context | 6/20 | 7/20 |
| deterministic "<file>, page N" context | 6/20 | 7/20 |

**A real cross-encoder does not help here.** `Qwen3-Reranker-0.6B` separates
relevant from irrelevant by four orders of magnitude in isolation (0.97 against
0.000016) and still loses to Reciprocal Rank Fusion end to end. Five HVAC
service manuals are near the worst case for reranking: every document genuinely
resembles every other, so there is little for a relevance judgement to separate.
Worth retrying on a mixed-document workspace; the table above is what it has to
beat. Note this is NOT the reranker deleted earlier — that one re-embedded
`content[:600]` with the same bi-encoder and was rightly removed.

**Contextual retrieval made it worse, twice, for two different reasons.**
Model-written context said "the heat-pump service manual", which is true of all
five manuals, and said it near-identically for every chunk of a page. Replacing
it with the exact filename failed too, and one query says why:

    to_tsvector('carrier_hch6_heatpump_service.pdf, page 62')
      -> 'carrier_hch6_heatpump_service.pdf':1  'page':2  '62':3
    to_tsquery('carrier') matches it?  -> FALSE

**The filename is one indivisible token.** Searching "carrier" cannot match it.
So the label added no discrimination while promoting `page` and a page number
above the content at weight 'A', in every chunk in the corpus. Anything put in a
weighted field has to survive tokenisation to be worth anything; split the
filename on underscores first if this is retried.

**Both context rows of the table above are void, found 2026-08-15.** Only the
embedding half of contextual retrieval was ever connected: the migration adding
`chunks.tsv` claims in its own comment to weight the context sentence and then
builds `to_tsvector('english', coalesce(content, ''))`, against a table with no
context column. The keyword arms never saw a context sentence, and storage kept
one sentence per page rather than one per chunk. The experiment was re-run
properly and contextual retrieval was then removed outright; see "Contextual
retrieval, measured properly and then removed". The filename-tokenisation
finding above stands on its own and is the more useful half of this anyway.

### What the failures point at

Nine of fourteen failures are "did not cite". Extraction and chunking are
verified correct on stored rows, so the bottleneck is retrieval, and none of the
usual levers moved it. Questions 6-8 ask "at 335 psig with 10 degrees of
subcooling" without naming a manufacturer, against five manuals holding five
near-identical charging charts. That is not a retrieval defect. It is a product
question: the workspace should scope to one unit, or the answer should ask which.

## Decisions not to re-litigate

One line each. The reasoning is in the commit or the code comment beside it.

- **Redis is a notification bus, never the queue.** Leases, retries and
  per-tenant fairness were each paid for in a bug. See `core/events.py`.
- **Publish after the commit, never inside it**, or the worker wakes and finds
  nothing.
- **Take Hermes 4's weights, not Hermes Agent.** Its memory is one pile shared
  across tenants, which is the thing we sell against.
- **`drive.file` and the picker, never `drive.readonly`**, which is a restricted
  scope needing a paid third-party security assessment.
- **Never store a standing credential to a customer's systems** unless there is
  no alternative. The Drive import holds a token for one request and nothing else.
- **Retrieval scopes by workspace, never by uploader.** Checking the uploader
  refused invited staff every document their owner had added.
- **Two questions, two functions.** *May you see it* is
  `accessible_workspace_ids`. *May you do it* is `assert_*_capability`. Every
  access bug so far has been answering one of them some other way.
- **A refusal is 404 when naming a resource by id**, so a stranger cannot
  enumerate what a company owns.
- **A document that extracts nothing fails loudly.** It used to be marked
  processed and answer nothing.
- **Do not widen CORS while credentials are allowed.**

## Known gaps

- **No database-level RLS.** Application-layer only, so every new route needs
  its explicit check. Scoped 2026-08-11: roughly a week, because sessions carry
  no tenant, the worker's queue poll is deliberately cross-tenant, five tables
  reach their tenant only through a join chain, and a policy on `chunks` lands
  on the vector search path. Deprioritised: `permissions.py` plus the refusal
  tests cover the same ground. Revisit when a security questionnaire asks in
  writing.
- **CSP is Report-Only.** Check the browser console for violations, then flip
  the header name. `frame-src` must keep `storage.googleapis.com` and
  `docs.google.com` or citations and the Drive picker break.
- **"Workflow automation" is marketed and does not exist.** It is the last item
  on the roadmap for a reason.
- **Nothing displays the vision verification flags yet.** They now reach
  `segments.meta_data` and come back from `hybrid_search` with every result, so
  the data is there. Whether a citation to an unverified figure page should say
  so, and how, is a design decision nobody has made.
- **A corpus can silently hold vectors from a retired embedding model.** Nothing
  detects this at runtime. `api/scripts/reembed_chunks.py --check` does, in one
  call per workspace; it is not wired into the deploy or into any alert.
- **A browser build value has to be correct in five places**: the workflow's
  env file, its build args, the Dockerfile `ARG` and `ENV`, the compose file,
  and the secret itself. Every one fails silently and green. The deploy now
  refuses rather than shipping a promise it cannot keep.

## Questions to bring to Osas, not guess at

- Rate limits (30/min chat, 10/min upload, per IP) are a first-pass guess.
- Whether Redis should be doing more than notifications and the answer cache.
- Whether the Report-Only CSP is clean enough to enforce.

