# SyntextAI — Engineering Overview

What is shipped, what is outstanding, and what not to build twice.

Git holds what changed and why. The code holds the reasoning beside the thing
being reasoned about. This file holds only the three lists above. If an entry
could be a commit message or a code comment, make it one instead.

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
Docker Desktop has lost file sharing**, not the repo. Seen 2026-08-26: the
long-running container had been "healthy" for ten days serving code it had
loaded into memory at startup, while the mount underneath it was unreadable, and
even `docker run -v /tmp:/m alpine ls /m` was denied. Grant Docker access again
in Docker Desktop's Settings under Resources, File Sharing, and in macOS System
Settings under Privacy & Security, Files and Folders. Until then nothing running
in a container sees edited code.

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
3. **Nothing here is an unkept promise.** Checked 2026-08-26. An earlier note
   claimed "workflow automation is marketed and does not exist" and it was
   wrong: that phrase is on osas-inc.com under **AI Implementation**, which is
   consulting work Osas delivers himself, and the same sentence says "powered by
   SyntextAI where it applies, custom-built everywhere else". syntextai.com
   claims only cited answers from your documents, which is what ships. Do not
   re-raise this as a liability.
4. **Vision verification flags have no UI.** The data reaches
   `segments.meta_data` and comes back from `hybrid_search` on every result.
   Whether a citation to an unverified figure should say so, and how, is an
   unmade design decision, not a build.
5. **Agent tool layer.** A fresh build, not a flag. `tool_agent.py` and
   `document_tools.py` were deleted in 40ca829 after the two systems scored 16.2
   and 17.0 calling the same `hybrid_search`, with three regressions from the
   code around them drifting.
6. **Activity history, admin dashboard, saved prompts.** Nothing exists.

**Not doing, decided 2026-08-26**
- **Email-in.** Proposed and dropped. Upload plus Drive covers ingress.
- **OneDrive / SharePoint picker.** The backend exists and is tested
  (`SharePointConnector`, registered in `_CONNECTORS`, `/files` accepts
  `provider: "sharepoint"`), so this is a frontend picker plus an Azure app
  registration whenever a customer asks. Not worth the effort before then.
- **Box, Dropbox.** Same reasoning.

**Known risks, carried deliberately**
- **No database-level RLS.** Application layer only, so every new route needs
  its explicit check. ~1 week to fix. Revisit when a security questionnaire asks
  in writing.
- **A workspace can silently hold vectors from a retired embedding model.**
  `api/scripts/reembed_chunks.py --check` detects it, wired into nothing.
- **Documents ingested before 2026-08-07 have a null `chunks.content`** and no
  fallback. Unreachable by keyword, unrepairable by re-embedding. Only fix is
  re-upload. `reembed_chunks --check` counts them.
- **Rate limits** (30/min chat, 10/min upload, per IP) are a first-pass guess.

**Parked, one customer away**

| Parked | Why |
|---|---|
| Slack, Teams | Need an admin, and SMBs mostly are not there |
| Computer-use on payer portals | Hardest on the list, credentials are heavy |
| Self-hosting models | A sales decision, not a cost one, at this size |

## Document currency, 2026-08-26

A workspace holding the 2019 cancellation policy and the 2024 one that replaced
it ranked them identically and cited whichever matched better. `files` carried
`created_at`, which is when somebody uploaded a file, and nothing that says when
a document became true. The customer's own feedback was the only thing that ever
noticed: *"cited the 2019 policy, we are on the 2024 one"*.

`files` now carries `effective_date` and `superseded_by_id`, and `hybrid_search`
skips any file with a replacement.

**The link points forward, from the old file to the new one.** Retrieval asks
"is this still current?" about every candidate row it has already joined `files`
for, so forward-pointing makes that a column test folded into the existing
WHERE. Backward-pointing would have been a NOT EXISTS subquery against `files`
per candidate, on the hot path, for the same answer.

**One clause, in `where_sql`, which all three retrieval arms share.** Excluding
the document in the vector arm alone would have let the keyword arm resurrect
it.

**The exclusion is skipped when the search is already scoped to one file.**
Asking about a specific document is an explicit request for that document,
replaced or not. "What did the old policy say" is a real question and refusing
to read a file the customer named would be a bug.

**ON DELETE SET NULL, not CASCADE.** Deleting the replacement brings the older
document back into answers rather than leaving it hidden with nothing pointing
at it. Tested rather than assumed.

**Cycles are refused at the route.** A replaced-by B and B replaced-by A hides
both documents from every answer, permanently, with nothing in the product able
to explain why. `supersede_chain_reaches` walks the chain, bounded at 64 hops.

**Held to `DELETE_DOCUMENT`, not `UPLOAD_DOCUMENT`.** Making a document stop
answering is a removal. Both file ids are authorized independently and the
replacement must be in the same workspace: the id arrives from the browser and
is not evidence of anything.

### What driving the app caught that the tests did not

Fifteen tests passed while `/api/v1/files` still returned the old six fields,
because that route rebuilds its own response dict rather than serialising what
the repository returns. Retrieval skipped the document and the list had no way
to say so, which is the exact silent state this feature exists to remove. There
is now a test on the list endpoint specifically.

`get_file_by_id` and `get_file_by_name` also held byte-identical copies of the
same serializer, so the fields would have reached one and not the other. Both
now call `_serialize_file`.

### Verification

273 tests pass, 16 of them new. The migration was cycled down and back up
against the local database. Driven end to end in the browser: marking a document
replaced removed it from search results for a query it was the only match for,
and undoing it brought it back.

## Documents SyntextAI writes, 2026-08-26

Ask for a document, get one written from the workspace's own documents, edit it,
and decide whether it joins the knowledge base. The deciding is the feature.

### The wall, and why it is a table rather than a boolean

A generated draft that could be retrieved makes the model its own source of
truth. It writes a plausible SOP with one wrong figure, that is ingested, and
afterwards it cites itself with a page reference indistinguishable from a real
one. Nobody reading the answer can tell.

So drafts live in `generated_documents`, which retrieval does not join and
cannot. A boolean on `files` would have been smaller and is the kind of thing a
future query forgets to check; this is unretrievable by construction. Proved by
a test that puts a figure appearing in no document into a draft and searches for
it.

Approving does not move a row. It writes the bytes to storage and creates an
ordinary `files` row queued for ingestion, so a draft is held to the plan limit,
the duplicate-name rule and the same worker as an upload. An approval is another
way in, not a way around.

### The rest of the decisions

- **The provenance note is inside the stored document**, not metadata beside it.
  Once approved it gets retrieved and read by people who were not in the room,
  and what they need to know is that a machine drafted it and a person approved
  it.
- **The screen says a machine wrote it, and keeps saying so after editing.** A
  document that looks like the business wrote it is the dangerous one.
- **Refuse rather than invent.** A request no document covers is refused with
  422, and gaps inside a draft are written as `TO BE COMPLETED: ...`. A gap the
  reader can see is useful; a gap filled with something plausible is dangerous,
  because staff follow it.
- **Held to `UPLOAD_DOCUMENT`**, and approval to the same. Owners manage
  documents, staff ask questions.
- **Chat history is opt-in** and read through the chat repository's own
  workspace scoping, so a history id from another tenant returns nothing.
- **"In the knowledge base" is both `status` and `ingested_file_id`.** Deleting
  the approved document nulls the id through ON DELETE SET NULL while status
  stays `ingested`; reading status alone would strand the draft, unable to be
  approved again.
- **The document opens in the main area, not a chat bubble.** A chat answer is
  the right size for a question and the wrong size for a document.

### Verification

292 tests pass, 19 new. Driven end to end against the local stack: generated a
real infection-control summary from `safe-care2.pdf` in 40 seconds, edited it,
saved it, confirmed a marker string added by hand was not retrievable, then
approved it and watched it become an ordinary document.

`GCS_CREDENTIALS_PATH` was added while doing that. The service-account path was
hardcoded in three places in `core/utils.py`, so nothing touching storage could
run outside the container: every upload, import and approval failed with
FileNotFoundError before reaching Google. The default is unchanged.

The first approval attempt failed for exactly that reason, which incidentally
proved the rollback: no `files` row was left behind, so no document appeared in
the list that could never be opened.

### Export to Word

`services/docx_export.py` converts the draft's markdown to .docx: H1-H4, ordered
and unordered lists including one level of nesting, pipe tables, and
bold/italic/code inline. Anything it does not recognise becomes an ordinary
paragraph rather than being dropped, because losing a line of a document
somebody is about to hand to staff is worse than styling it plainly. A table
whose rows disagree about their column count is a truncated table, and becomes
text rather than an exception in the middle of a download.

No markdown-to-docx library was added. `python-docx` is already here because
`DocxProcessor` reads Word files on the way in, and pulling in a dependency to
convert six constructs is a poor trade.

**The provenance note is written into the file, above the content.** A .docx
leaves the product: it gets emailed, printed and pinned to a wall, and that is
exactly when everybody forgets a machine drafted it. Marking it only in the app
marks it in the one place it is already obvious.

The title reaches a `Content-Disposition` header and is customer text, so
`safe_filename` strips it to characters that cannot break out of a header value.
Tested with a title containing a quote and a CRLF.

**Two faults the first real export exposed**, both fixed and both invisible to
the tests that existed:

- The model wrote `(Segment 6)` and `(Segment 9)` into a printed procedure. The
  numbering is scaffolding for the model, and a staff member following an SOP
  has never heard of a segment. The prompt now forbids it explicitly and shows
  the model what not to write. Three real runs since: zero references.
- The title was the raw prompt, so a document was called "A hand hygiene quick
  reference for new staff. Use a table for when to wash..." and downloaded under
  that filename. The draft now takes its name from its own opening `# heading`
  when it wrote one, falling back to the prompt. The .docx skips that heading so
  the name is not printed twice.

### Not done

No PDF. Word only.

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
