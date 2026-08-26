# SyntextAI — Engineering Overview

What is shipped, what is outstanding, and what not to build twice.

Git holds what changed and why. The code holds the reasoning beside the thing
being reasoned about. This file holds only the three lists above. If an entry
could be a commit message or a code comment, make it one instead.

**Under 300 lines. Check before committing.** It was cut from 960 to 260 on
2026-08-26 and was back to 538 by that evening, because four features each
arrived with a dated section retelling its own commit message. That is the
changelog this file says it is not.

A shipped thing is one line under **Shipped**. What it taught is one line under
**Decisions not to re-litigate**, with the number that makes it worth obeying.
Neither is a section with a date in the heading. If it needs more room than
that, the room is the commit message.

## What it is

Internal documents, made answerable by a whole team, every answer cited to the
page it came from. Customers are small businesses, 10 to 100 people, in
document-heavy work: dental, accounting, legal, property management, trades,
insurance. Live and taking money since 2026-08-03.

**The test for anything new:** can the person who benefits say yes by
themselves? If yes, build it. If somebody else has to approve it first, park it
until a customer asks by name.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, async SQLAlchemy, PostgreSQL + pgvector, Alembic |
| Frontend | React + TypeScript, Firebase Auth, Stripe, HashRouter |
| Storage | Google Cloud Storage |
| LLM | DeepInfra, `openai-gpt-oss-20b`, temp 0.1, 120k window |
| Embeddings | Voyage AI |
| Email | SendGrid |
| Infra | DigitalOcean droplet, nginx, Docker Compose |
| Analytics | PostHog |

Retrieval settings in force: `MAX_RETRIEVALS 1`, `RETRIEVAL_TOP_K 25`, candidate
pool 100, fusion weights 0.7 vector / 0.3 keyword.

## Local dev

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev up --build -d
```

**Always pass `--env-file .env.dev`.** Compose interpolates build args from
`.env` by default, which is the production file, so without the flag the
frontend is built with the **live** Stripe key and test checkout silently fails.
`.env` is `sk_live`/`pk_live` and belongs to deploys. `.env.dev` is
`sk_test`/`pk_test` and belongs to the local stack. Never hand-swap them.

Migrations:

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev run --rm --no-deps -w /app/api --entrypoint sh syntextaiapp -c "alembic upgrade head"
```

Rebuild with `--build` after any `requirements.txt` change. The `./api:/app/api`
mount refreshes code but not installed packages. See `env.example` for every
config value.

**If a container reports "Permission denied" on every file under `/app/api`,
run `colima stop && colima start`.** Local Docker here is Colima, not Docker
Desktop, and its `mountType` is sshfs: the mount runs over an SSH connection
into the VM, and after the VM has been up a long time that connection dies. The
mountpoint survives, so `ls` works and every read fails, and containers already
running keep serving code they loaded at startup. Seen 2026-08-26, where a
worker had looked healthy for two weeks while running a fortnight-old schema
against the live database.

Nothing to do with macOS privacy settings. There is a leftover Docker Desktop
`settings.json` on this machine that controls nothing; reading it cost three
wrong diagnoses.

## Shipped

Live in production.

**Product**
- Ask a question, get an answer cited to a page. PDF, DOCX, TXT, MD.
- **Find**: search returning matched pages in ~0.6s, no generation.
- Vision extraction: PDF pages the text layer loses are read by a vision model.
- Organizations, workspaces, invites, roles, per-workspace document access.
- Stripe: trial, subscribe, plan changes, 3D Secure, card update.
- Answer feedback: thumbs, four reasons, a comment, joined to the run.
- **Usage** in Settings: questions, who asks, documents including failed ones,
  what the team thought, which documents nothing has ever used, ingestion time.
- Import from Google Drive through the picker, `drive.file` only.
- **Document currency**: mark a document as replaced by a newer one, and it
  stops answering questions. See below.
- **Vision cautions reach the reader**: a page read off a figure that the text
  layer could not confirm now says so, in the answer and on the citation.
- **Documents SyntextAI writes**: ask for an SOP or a summary, get it written
  from the workspace's own documents, edit it, download it as Word, and approve
  it into the knowledge base. Approval is the only way one ever answers a
  question. See below.

**Underneath**
- Durable job queue in Postgres: leases, retries, per-tenant fairness, separate
  query and ingest budgets.
- Redis as a notification bus. A queued job starts in ~0.2s instead of 10s.
- Short-lived answer cache, invalidated when a workspace's documents change.
- Ingestion writes batch by batch: a crash keeps the pages it reached, and a
  document is searchable while it finishes.
- Text embedded once per organization. Re-uploads cost nothing.
- Security: CORS locked, CSP enforced, rate limits on paid endpoints, security
  headers, no exception text to clients, signed document URLs.

## Outstanding

In order. The bet is depth on documents we already hold, not more ways to get
documents in. Ingress today is upload plus the Google Drive picker, and that is
enough until a customer says otherwise.

1. **Memory.** Standing facts per organization and workspace, editable, seeded
   from the feedback that already arrives. `message_feedback.reason` and
   `.comment` are collected today and read by nobody. *"Cited the 2019 policy,
   we are on the 2024 one"* is a standing fact being handed over and thrown
   away. The other end of document currency, now shipped.
2. **MCP server.** Expose `hybrid_search` over one workspace so a customer's own
   Claude can query their knowledge base. No new ingestion, and `hybrid_search`
   plus the tenant scoping already exist. Distribution more than a feature.
3. **Agent tool layer.** A fresh build, not a flag. `tool_agent.py` and
   `document_tools.py` were deleted in 40ca829 after the two systems scored 16.2
   and 17.0 calling the same `hybrid_search`, with three regressions from the
   code around them drifting.
4. **Activity history, admin dashboard, saved prompts.** Nothing exists.

**Not doing, decided 2026-08-26**
- **Email-in.** Proposed and dropped. Upload plus Drive covers ingress.
- **OneDrive / SharePoint picker.** The backend exists and is tested
  (`SharePointConnector`, registered in `_CONNECTORS`, `/files` accepts
  `provider: "sharepoint"`), so this is a frontend picker plus an Azure app
  registration whenever a customer asks. Not worth the effort before then.
- **Box, Dropbox.** Same reasoning.

**Known risks, carried deliberately**
- **No database-level RLS.** Application layer only, so every new route needs
  its explicit check. Revisit when a security questionnaire asks in writing.
  A week, and mostly code: the app connects as `syntext`, a SUPERUSER, and
  Postgres bypasses every policy for superusers, so a new role comes first or
  the policies do nothing at all. Then every session must set and reliably clear
  the tenant, because a leaked setting on a pooled connection means one request
  inherits another's. `test_every_route_is_scoped.py` covers the realistic
  failure meanwhile.
- **Rate limits** (30/min chat, 10/min upload, per IP) are a first-pass guess.

**Parked, one customer away**

| Parked | Why |
|---|---|
| Slack, Teams | Need an admin, and SMBs mostly are not there |
| Computer-use on payer portals | Hardest on the list, credentials are heavy |
| Self-hosting models | A sales decision, not a cost one, at this size |

## Decisions not to re-litigate

Reasoning is in the commit or the code comment beside it.

**Architecture**
- Redis is a notification bus, never the queue. Leases, retries and per-tenant
  fairness were each paid for in a bug.
- Publish after the commit, never inside it, or the worker wakes to nothing.
- Retrieval lives in SQL (`async_file_repository.hybrid_search`), not Python.
  RAGFactory and the interfaces were DI scaffolding for two concrete classes.
- Ranks are fused, never scores. A cosine similarity and a `ts_rank_cd` share no
  scale.
- Ingestion makes no chat call. Embeddings and vision only.

**Security**
- Two questions, two functions. *May you see it* is `accessible_workspace_ids`.
  *May you do it* is `assert_*_capability`. Every access bug so far answered one
  of them some other way.
- Retrieval scopes by workspace, never by uploader.
- A refusal is 404 when naming a resource by id, so a stranger cannot enumerate
  what a company owns.
- `drive.file` and the picker, never `drive.readonly`, which is a restricted
  scope needing a paid third-party assessment.
- Never store a standing credential to a customer's systems.
- Do not widen CORS while credentials are allowed.
- A document that extracts nothing fails loudly. It used to be marked processed
  and answer nothing.

**Documents, and documents we write** (2026-08-26)
- **A supersede link points forward**, from the old file to the new. Retrieval
  has already joined `files` for every candidate, so it is a column test in the
  existing WHERE rather than a NOT EXISTS per candidate on the hot path.
- **The exclusion is skipped when a search is scoped to one file.** Asking about
  a document is an explicit request for it, replaced or not.
- **Drafts live in their own table, not a flag on `files`.** Retrieval joins
  `files` and cannot join that table, so a generated draft is unretrievable by
  construction. A boolean is what a future query forgets. If a draft could be
  retrieved, the model's output becomes its own source, citing itself with a
  page reference nobody can tell from a real one.
- **Approving writes an ordinary `files` row through the upload path.** An
  approval is another way in, not a way around, so it meets the plan limit and
  the duplicate-name rule like anything else.
- **The provenance note goes inside the exported file**, not beside it. A .docx
  gets emailed and printed, which is exactly when everyone forgets a machine
  drafted it.
- **Drafting does not go through `generate_explanation`.** That hardcodes 1500
  completion tokens, right for an answer. Reasoning spends the same allowance, so
  a two-page SOP reasoned through the budget and returned empty about one attempt
  in four. `DRAFT_MAX_TOKENS` 4000, `DRAFT_REASONING_EFFORT` low.
- **One markdown parser, two renderers.** Word and PDF share it. Two parsers for
  one grammar drift, and `ordered` is per ITEM: numbered steps with bulleted
  notes are one list, and splitting them numbered every step 1.

**Reading what the server sends** (2026-08-26)
- **The API serialises naive UTC with no timezone designator, and JavaScript
  parses such a string as LOCAL.** Every timestamp was wrong by the viewer's
  offset until `utils/serverTime.ts`. Parse server times through it, always.
- **Claim the connection slot before awaiting a token.** `initializeWebSocket`
  checked its guard before an await and assigned after, so two calls both opened
  a socket and the first was orphaned mid-handshake, logging a failure on every
  page load while the app was connected.

**Vectors and documents that cannot answer** (2026-08-26)
- **`chunks.embedding_model` records which model wrote each vector.** The
  voyage-3.5-lite to Qwen3-Embedding-0.6B move errored nowhere and cost 11 of 17
  benchmark questions their source. Next change is a column comparison.
- **A NULL `embedding_model` is "not measured", not "stale".** Counting NULL as
  stale flagged 2,528 of 2,650 chunks locally: a warning on nearly every
  document, which is no signal.
- **Production was measured 2026-08-26 and is clean.** 881 chunks, 2 documents,
  0 with null `content`, all ingested after the model move. Detection and a UI
  badge for that fault were built and deleted the same day: a new dead chunk
  cannot be created since `chunks.content` started being written 2026-08-06. The
  lesson is that "how many are there in production" was a database query
  available the whole time, asked after the code rather than before it.

**Authorization** (2026-08-26)
- **Never scope by uploader.** Both file-status endpoints checked
  `file_record["user_id"]`, so a staff member's list silently omitted every
  document the owner uploaded and those files sat at Processing forever.
- **`test_every_route_is_scoped.py` fails on a route that reaches no tenant.**
  A smoke alarm, not a sprinkler: it reads source, so it proves a handler
  consults the boundary, not that it consults it correctly.

**Measured and rejected.** Do not rebuild these without beating the number.
- **Cross-encoder reranking.** Qwen3-Reranker-0.6B separates relevant from
  irrelevant by four orders of magnitude in isolation and changed nothing end to
  end: 7/20 answers and 9/20 citations with it on and off.
- **Contextual retrieval.** One LLM call per chunk at ingest (~1,500 for a
  500-page manual). Retrieved nothing extra, ranked slightly worse, and
  destroyed the embedding-reuse saving.
- **Four retrieval ideas, 2026-08-14.** All four rejected on one corpus,
  repeatable to the question.
- **An LLM coverage classifier.** Regressed the agent from 16.2 to 11.2 while
  looking reasonable on four questions. Coverage is decided by whether a search
  contributed new passages, not by judgement.
- **The slowness was the provider, not the code.** Three code-level
  explanations were wrong before moving to DeepInfra on 2026-08-13 fixed it.

## Questions for Osas, not to guess at

- Are the rate limits right?
- Should Redis do more than notifications and the answer cache?
