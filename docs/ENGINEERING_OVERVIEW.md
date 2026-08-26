# SyntextAI — Engineering Overview

What is shipped, what is outstanding, and what not to build twice.

Git holds what changed and why. The code holds the reasoning beside the thing
being reasoned about. This file holds only the three lists above. If an entry
could be a commit message or a code comment, make it one instead.

**Under 250 lines. Check before committing.** It was 960, then 260, then 538 in
a single day, because each feature arrived with a dated section retelling its own
commit message. That is the changelog this file says it is not.

A shipped thing is one line under **Shipped**. What it taught is one line under
**Decisions**, with the number that makes it worth obeying. Neither is ever a
section with a date in the heading. If it needs more room, the room is the commit
message.

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
| Embeddings | Qwen3-Embedding-0.6B on DeepInfra |
| Email | SendGrid |
| Infra | DigitalOcean droplet, nginx, Docker Compose |
| Analytics | PostHog |

Retrieval settings in force: `MAX_RETRIEVALS 1`, `RETRIEVAL_TOP_K 25`, candidate
pool 100, fusion weights 0.7 vector / 0.3 keyword.

## Local dev

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev up --build -d
```

**No Node needed on the host.** The frontend is built inside the image, on
node:20-alpine. Vite 8 needs 20.19+ and that image is 20.20.2. Only reach for a
host Node if you want to run `npm run dev` outside compose, and then it has to
meet the same floor.

**Always pass `--env-file .env.dev`.** Compose interpolates build args from
`.env`, the production file, so without the flag the frontend is built with the
**live** Stripe key and test checkout fails silently.

Migrations:

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev run --rm --no-deps -w /app/api --entrypoint sh syntextaiapp -c "alembic upgrade head"
```

**"Permission denied" on every file under `/app/api` means the mount died.**
Docker here is Colima and its mountType is sshfs, which drops once the VM has
been up a while. `colima stop && colima start`. Nothing to do with macOS privacy
settings. Containers already running keep serving code they loaded at startup,
so they look healthy while running a stale schema.

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

One line each. The reasoning is in the commit or the code comment beside it.

**Architecture**
- Redis is a notification bus, never the queue.
- Publish after the commit, never inside it, or the worker wakes to nothing.
- Retrieval lives in SQL (`hybrid_search`), not Python.
- Ranks are fused, never scores: a cosine and a `ts_rank_cd` share no scale.
- Ingestion makes no chat call. Embeddings and vision only.
- One markdown parser, two renderers, for Word and PDF export.

**Security and tenancy**
- Two questions, two functions. *May you see it* is `accessible_workspace_ids`.
  *May you do it* is `assert_*_capability`.
- Scope by workspace, never by uploader. Checking the uploader refused invited
  staff every document their owner added, twice, in two different places.
- A refusal is 404 when naming a resource by id.
- `test_every_route_is_scoped.py` fails on a route that reaches no tenant.
- `drive.file` and the picker, never `drive.readonly`.
- Never store a standing credential to a customer's systems.
- Do not widen CORS while credentials are allowed.
- A document that extracts nothing fails loudly.

**Documents we write**
- Drafts live in their own table, not behind a flag on `files`. Retrieval joins
  `files` and cannot join that table, so a draft is unretrievable by
  construction. Otherwise the model's output becomes its own source, citing
  itself with a page reference nobody can tell from a real one.
- Approving writes an ordinary `files` row through the upload path, so it meets
  the plan limit and the duplicate-name rule like anything else.
- The provenance note goes inside the exported file. A .docx gets emailed and
  printed, which is when everyone forgets a machine drafted it.
- Drafting does not go through `generate_explanation`: its 1500-token budget is
  right for an answer, and reasoning spends the same allowance, so a two-page SOP
  returned empty about one attempt in four.
- A supersede link points forward, so retrieval tests a column it already joined.

**Reading what the server sends**
- The API serialises naive UTC with no designator and JavaScript parses that as
  LOCAL. Every timestamp was wrong by the viewer's offset. Use
  `utils/serverTime.ts`, always.
- Claim the websocket slot before awaiting the token, or two sockets open and the
  first is orphaned mid-handshake.

**Vectors**
- `chunks.embedding_model` records which model wrote each vector. Moving off the
  previous model errored nowhere and cost 11 of 17 benchmark questions their
  source. Next change is a column comparison.
- NULL `embedding_model` is "not measured", not "stale". Counting NULL as stale
  flagged 2,528 of 2,650 chunks: a warning on nearly every document, so no signal.

**Measured and rejected.** Do not rebuild without beating the number.
- Cross-encoder reranking: 7/20 answers with it on and off.
- Contextual retrieval: ~1,500 LLM calls per 500-page manual, retrieved nothing
  extra, ranked slightly worse.
- An LLM coverage classifier: regressed the agent 16.2 to 11.2.
- Four retrieval ideas, 2026-08-14, all rejected on one corpus.
- The slowness was the provider, not the code. Three code-level explanations were
  wrong before moving to DeepInfra fixed it.

## Questions for Osas, not to guess at

- Are the rate limits right?
- Should Redis do more than notifications and the answer cache?
