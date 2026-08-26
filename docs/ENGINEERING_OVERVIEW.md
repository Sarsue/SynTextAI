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
3. **Nothing here is an unkept promise.** Checked 2026-08-26. An earlier note
   claimed "workflow automation is marketed and does not exist" and it was
   wrong: that phrase is on osas-inc.com under **AI Implementation**, which is
   consulting work Osas delivers himself, and the same sentence says "powered by
   SyntextAI where it applies, custom-built everywhere else". syntextai.com
   claims only cited answers from your documents, which is what ships. Do not
   re-raise this as a liability.

4. **Agent tool layer.** A fresh build, not a flag. `tool_agent.py` and
   `document_tools.py` were deleted in 40ca829 after the two systems scored 16.2
   and 17.0 calling the same `hybrid_search`, with three regressions from the
   code around them drifting.
5. **Activity history, admin dashboard, saved prompts.** Nothing exists.

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

  It is a week, and the week is code rather than schema. Enabling RLS and
  writing a policy per table is an hour. The app must then stop connecting as
  `syntext`, which is a SUPERUSER: Postgres bypasses every policy for
  superusers, so without a new role the policies do nothing at all. Then every
  session has to set the tenant and reliably clear it, because connections are
  pooled and a leaked setting means one request inherits the previous request's
  tenant, which is worse than having none. The worker's queue poll is
  deliberately cross-tenant and needs an explicit bypass, five tables reach
  their tenant only through a join chain, and a policy on `chunks` lands on the
  vector search path.

  `test_every_route_is_scoped.py` covers the realistic failure in the meantime:
  it fails when a new route neither reaches its tenant nor says in EXEMPT why it
  needs none. That is a smoke alarm, not a sprinkler. It reads source rather
  than behaviour, so it proves a handler CONSULTS the boundary, not that it
  consults it correctly, and it does nothing about a bad query inside an
  authorized route. Verified to fail on a deliberately unscoped route before
  being trusted.
- **The next embedding model change will be visible.** `chunks.embedding_model`
  records which model wrote each vector, written on ingest and stamped by
  `reembed_chunks.py` when it repairs. The move from voyage-3.5-lite to
  Qwen3-Embedding-0.6B errored nowhere, cost 11 of 17 benchmark questions their
  source, and was found by hand days later. Next time it is a column
  comparison.

  **Measured on production 2026-08-26, and it is clean.** 881 chunks across 2
  documents in one workspace, 0 with a null `content`, everything ingested
  2026-08-15, which is after the model move. The two files that predate it never
  ingested at all. Retrieval verified against the live API: "service procedure"
  returns 20 pages, "torque" and "temperature" both hit.

  Detection and a UI badge for those faults were built and then removed the same
  day. A new dead chunk cannot be created since `chunks.content` started being
  written on 2026-08-06, so that half guarded a condition that neither exists nor
  can recur. The lesson is not "do not build defences", it is that the question
  "how many are there in production" was a database query available the whole
  time, and it was asked after the code rather than before it.

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

`files` now carries `superseded_by_id`, and `hybrid_search` skips any file with
a replacement.

An `effective_date` column shipped here first and was removed before release.
Nothing set it, nothing displayed it, and retrieval did not read it: a date that
looks like it decides which document answers, and does not, is a trap. The
supersede link answers "which one is true" definitively and is enforced in the
query. If a customer asks to see effective dates, that is a column plus a UI
plus a decision about ranking, and it starts from measurement.

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
- **Writing is a third composer mode, beside Ask and Find.** It shipped with its
  own textarea and its own submit button in the sidebar, which was a second
  place to type the same shape of request. `Ask / Find / Write` is the
  segmented control the app already had, the mode is hidden from staff because
  the server refuses them anyway, and the offer to write from the conversation
  now sits under the conversation it refers to. The sidebar keeps the LIST,
  because a document you wrote last week is something you own and go back to,
  and finding it by scrolling chat history would be worse.

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

### Export to Word and PDF

`services/document_export.py` reads the markdown ONCE into a list of blocks and
each format renders those blocks its own way. The alternative was a second
markdown parser inside the PDF writer, and two parsers for one grammar drift:
teach one about nested lists and the other silently keeps flattening them, and
nobody finds out until a customer's SOP prints wrong.

Word is written with `python-docx`. PDF is laid out by MuPDF's `Story`, which
takes a small HTML subset and paginates it, on US Letter with one inch margins.
Neither needed a new dependency: `python-docx` is here because `DocxProcessor`
reads Word files on the way in, and PyMuPDF because `PDFProcessor` reads PDFs.
No markdown-to-docx or HTML-to-PDF library was added to convert six constructs.

Both formats handle: H1-H4, ordered
and unordered lists including one level of nesting, pipe tables, and
bold/italic/code inline. Anything it does not recognise becomes an ordinary
paragraph rather than being dropped, because losing a line of a document
somebody is about to hand to staff is worse than styling it plainly. A table
whose rows disagree about their column count is a truncated table, and becomes
text rather than an exception in the middle of a download.

**The provenance note is written into both files, above the content.** They
leave the product: they get emailed, printed and pinned to a wall, and that is
exactly when everybody forgets a machine drafted it. Marking it only in the app
marks it in the one place it is already obvious.

**Text is escaped before markdown becomes HTML, not after.** The drafting prompt
asks for gaps written as `TO BE COMPLETED: <what is missing>`, so a document
full of angle brackets is the normal case. Escaping afterwards would hand MuPDF
an unknown tag and the rest of the paragraph would vanish.

The title reaches a `Content-Disposition` header and is customer text, so
`safe_filename` strips it to characters that cannot break out of a header value.
Tested with a title containing a quote and a CRLF.

**Three faults the first real exports exposed**, all fixed, and all invisible to
the tests that existed, because those tests fed the converter markdown somebody
had already written rather than markdown the model produced:

- The model wrote `(Segment 6)` and `(Segment 9)` into a printed procedure. The
  numbering is scaffolding for the model, and a staff member following an SOP
  has never heard of a segment. The prompt now forbids it explicitly and shows
  the model what not to write. Three real runs since: zero references.
- The title was the raw prompt, so a document was called "A hand hygiene quick
  reference for new staff. Use a table for when to wash..." and downloaded under
  that filename. The draft now takes its name from its own opening `# heading`
  when it wrote one, falling back to the prompt. The file skips that heading so
  the name is not printed twice.
- **A five-step procedure printed as 1, 1, 1, 1, 1.** Numbered steps with
  bulleted notes under them are how every SOP is written, and the block held one
  `ordered` flag for the whole list, so the parser ended the list and started
  another at every switch and the numbering restarted. `ordered` is now a
  property of each ITEM. A blank line no longer ends a list either, unless what
  follows is not a list item, because markdown allows loose lists and ending one
  there restarts the count too. Caught by looking at the rendered page, not by
  reading the extracted text, which showed the right words in the right order.

### Not done

Nothing on this feature. Both formats ship.

## Finishing the two features, 2026-08-26

Both shipped with edges left open. Every one below is closed; they are recorded
because each was invisible from the tests and visible the moment somebody used
the app.

**A dead column.** `files.effective_date` had a migration, an ORM field, an API
field and a TypeScript type, and no reader anywhere. Removed rather than
completed, for the reason above. The migration was amended rather than followed
by a drop, because none of this had reached master.

**Writing a document failed roughly one attempt in four.** Drafting went through
`generate_explanation`, which hardcodes 1500 completion tokens because that is
right for a chat answer. Reasoning is spent from the SAME allowance as the
output, so a two-page SOP with a table sometimes reasoned through the whole
budget and returned an empty string: the request succeeded, `_has_content`
rejected it, all three attempts failed, and the customer got "Could not write
the document. Try again." Drafting now calls `gradient_chat` directly with
`DRAFT_MAX_TOKENS` (4000) and `DRAFT_REASONING_EFFORT` (low). Two failures in
about seven attempts before; six for six after. The identical trap is recorded
in `llm_service` beside `reasoning_effort`, where raising the answer path to
medium silently broke query expansion.

**Writing from the conversation was unreachable.** `history_id` was built,
tenant-scoped and never sent by the app, and had no test. There is now a
checkbox, offered only when a conversation is on screen and never applied
silently, and three tests: the conversation reaches the prompt when asked for,
does not when it is not, and a conversation belonging to another workspace is
not read at all.

**The drafts list was fetched once per mount.** A document written in another
tab, or approved from the document view, stayed invisible until the whole page
was reloaded. It now reloads when the panel is opened and when the tab comes
back to the front, and pages beyond the first twenty.

**Opening a document hid the chat with no reliable way back.** The X sits in a
corner a toast can cover, which is how this was found: there was a moment with
no reachable route to the answer. Escape closes it now, except while a
confirmation is up or a field has focus, and asking a question or running a
search closes it too, because the result renders where the document was.

**The request that produced a draft was stored and never shown**, though the
schema comment claimed it was kept so the customer could see it. Now a collapsed
"What this was asked for".

**An unsettable `title` on the generate endpoint.** A draft names itself from
its own opening heading and the document view renames it, so a third route was
an argument nothing could pass.

### One that was not ours, and two wrong diagnoses before it

Every local session logged "WebSocket connection failed" while the indicator
read Connected. The first guess was that Vite does not proxy `ws://`; it does,
with `ws: true`. The second was React StrictMode double-mounting; there is no
StrictMode in this app.

The actual cause: `initializeWebSocket` checked `socketRef.current` BEFORE
awaiting `getIdToken()` and assigned it after, so two calls both saw null, both
opened a socket, and the first was orphaned mid-handshake with nothing holding a
reference to close it. A `connectingRef` claimed synchronously fixes it.
Measured after: one socket per page load, accepted and authenticated, where
there had been two.

## The vision caution, 2026-08-26

`_read_page_with_vision` makes a costly, deliberate choice. On a page whose text
layer is a credible record, a transcription introducing numbers absent from the
page is rejected outright, because a confident wrong torque value is a safety
claim rather than a typo. On a figure-dominant page the text layer is incomplete
rather than merely disordered, so it cannot arbitrate: the read is KEPT and
flagged. Enforcing the strict rule there cost four benchmark questions on
2026-08-14.

"Flagged" was doing no work. The flag reached `segments.meta_data`, came back
from `hybrid_search` with every result, was read into a local named `meta` in
`_format_context_and_sources`, and was never used again. Nothing in the frontend
referenced `meta_data` at all. So a page kept PRECISELY because nothing could
check it produced a citation identical to a verified one: same link, same page
number, same confidence.

Now told in both places, because they fail differently.

**The model is told, in the segment header it already reads.** It is the only
thing that knows which figure it is about to quote, and instruction 8 tells it
to say so in one short sentence naming what should be checked. Only for headers
carrying the flag: cautioning everything is the same as cautioning nothing, and
there is a test for that.

**The citation says so too**, because a reader may skip the sentence and click
the link. The link still resolves to the page; a caution is not a reason to
break a citation.

**And the box says once, in words, what the marker means.** "Unverified" beside
a filename is easy to read straight past.

Five tests, including one asserting that one unverified page among three does
not taint the other two.

### The reason to do it at all

This is the defence, not the disclaimer. Every competitor's pitch is that their
answers can be trusted; Guru's homepage headline is literally about confidently
wrong AI. A product that says which of its own answers it could not verify is
making a stronger claim than one that says nothing, and it is the claim a
compliance-minded buyer actually wants.

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
