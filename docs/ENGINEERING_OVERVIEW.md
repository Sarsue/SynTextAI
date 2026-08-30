# SyntextAI — Engineering Overview

What is shipped, what is outstanding, and what not to build twice.

Git holds what changed and why. The code holds the reasoning beside the thing
being reasoned about. If an entry could be a commit message or a code comment,
make it one instead.

**Under 250 lines. Check before committing.** It was 960, then 260, then 538 in
a single day, because each feature arrived with a dated section retelling its
own commit message. A shipped thing is one line under **Shipped**; what it
taught is one line under **Decisions**, with the number that makes it worth
obeying. Never a section with a date in the heading.

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

**No Node needed on the host**, for building or for driving the UI. The frontend
is built inside the image on node:20-alpine. Vite 8 needs 20.19+ and that image
is 20.20.2, while the host has 18.15.0.

**A driven browser is a hidden page**, so animations finish without firing
`animationend`. A Radix dialog waiting to unmount looks stuck and is not.

**To drive the UI, use the dev server on :5173, not :3000.**

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev --profile dev up -d frontend-dev
```

Port 3000 is a production build, so it has no dev sign-in harness and cannot be
signed into without a real Google account. 5173 has the harness and hot reload.
Sign in with `window.__syntextDevSignIn(customToken)`; mint it with the
service-account key inside the app container and destroy it afterwards.

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

**A new route answering 405 means the same thing.** `./api` is mounted, so the
files are current, but uvicorn registered its routes at startup.
`docker compose ... restart syntextaiapp`. Tests will not catch this: they
import the app fresh.

## Shipped

Live in production.

**Product**
- Ask a question, get an answer cited to a page. PDF, DOCX, TXT, MD.
- **Find**: search returning matched pages in ~0.6s, no generation.
- Vision extraction: PDF pages the text layer loses are read by a vision model.
- Organizations, workspaces, invites, roles, per-workspace document access.
  A pending invite can be cancelled, which kills the link already in the inbox.
- Stripe: trial, subscribe, plan changes, 3D Secure, card update.
- Answer feedback: thumbs, four reasons, a comment, joined to the run.
- **Usage** in Settings: questions, who asks, documents including failed ones,
  what the team thought, which documents nothing has ever used, ingestion time.
- Import from Google Drive through the picker, `drive.file` only.
- **Document currency**: mark a document replaced by a newer one and it stops
  answering questions.
- **Vision cautions reach the reader**: a page read off a figure the text layer
  could not confirm says so, in the answer and on the citation.
- **Team history**: invites, joins, removals and access changes, in the Team
  panel. A removal used to leave no trace anywhere.
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

**Deep-linked citations** (`/#/doc/:fileId/:page`, 2026-08-29)

A citation with an address. The viewer had always been a dialog inside `/chat`
opened by React state, so "this document at this page" had no URL and could not
be bookmarked, shared, or reached from outside the app.

MCP exposed the gap but is not the reason it is general. Any surface that
carries a citation as text rather than a click needs this: MCP today, Teams,
WhatsApp, a browser extension or an emailed answer next. All of them use the
same URL, which is why the route is `/doc` and not `/mcp/doc`. It also gives
the web app the shareable citation link it never had.

The viewer, the signing and the authorization are reused untouched. The link is
a reference and not a grant: following it signs the reader in and mints the
storage URL per request after the workspace check. The signed URL is
deliberately absent from MCP output, because that would put a credential-free
link to a private document into a transcript and would expire in 30 minutes.

*What did not work, and why it matters more than the feature.* Driving this in
an automation browser showed the PDF opening at page 1 instead of the cited
page, and it was reverted on that reading. The reading was worthless: the
existing citation click, which works in production, does exactly the same thing
in that browser, because Chrome drops `#page=N` on a cold cross-origin PDF
load. The control test was never run before the conclusion. Three separate
findings this session came from environment artifacts of this kind, including a
hidden browser pane reporting a 0px viewport. **Run the known-good path through
the same harness before believing a negative result.**

## Outstanding

In order. The bet is depth on documents we already hold, not more ways to get
documents in. Ingress today is upload plus the Google Drive picker, and that is
enough until a customer says otherwise.

1. **Memory.** *Paused 2026-08-28: Osas is not convinced, and it has not been
   argued back.* Standing facts per organization and workspace, editable, seeded
   from the feedback that already arrives. `message_feedback.reason` and
   `.comment` are collected today and read by nobody. *"Cited the 2019 policy,
   we are on the 2024 one"* is a standing fact being handed over and thrown
   away. The other end of document currency, now shipped. Do not start this
   without asking again.
2. **MCP server.** Expose `hybrid_search` over one workspace so a customer's own
   Claude can query their knowledge base. No new ingestion, and `hybrid_search`
   plus the tenant scoping already exist. Distribution more than a feature.

   All four phases shipped: the credential and the Principal, the Connections
   screen, the MCP server, and the OAuth authorization server. Deployed
   2026-08-28, plus deep-linked citations 2026-08-29. Driven end to end against
   production through a real connector. See "Credentials" and "The authorization
   server" below.
3. **Agent tool layer.** A fresh build, not a flag. `tool_agent.py` and
   `document_tools.py` were deleted in 40ca829 after the two systems scored 16.2
   and 17.0 calling the same `hybrid_search`, with three regressions from the
   code around them drifting.
4. **Admin dashboard, saved prompts.** Nothing exists. Team history shipped.

**Not doing, decided 2026-08-26**
- **Email-in.** Proposed and dropped. Upload plus Drive covers ingress.
- **OneDrive / SharePoint picker.** Backend exists and is tested, so this is a
  frontend picker plus an Azure app registration whenever a customer asks.

**Known risks, carried deliberately**
- **No database-level RLS.** Application layer only, so every new route needs
  its explicit check. Revisit when a security questionnaire asks in writing.
  A week, and mostly code: the app connects as a SUPERUSER, which bypasses every
  policy, so a new role comes first. Then every session must set and reliably
  clear the tenant, or a pooled connection leaks one request's into another's.
  `test_every_route_is_scoped.py` covers the realistic failure meanwhile.
- **Rate limits** (30/min chat, 10/min upload, per IP) are a first-pass guess.

**Parked, one customer away**

| Parked | Why |
|---|---|
| Slack, Teams | Need an admin, and SMBs mostly are not there |
| Computer-use on payer portals | Hardest on the list, credentials are heavy |

**Ingestion review sprint** (2026-08-30)

The finding, before the numbers: **the pipeline discarded structure the PDF
already contained, then paid a model to guess it back.** `page.get_text()`
returns a flat string on line one. The digit-ratio gate, the vision model and
the numeric guardrail were all machinery to detect and repair that loss.

Measured across five real manuals, 406 pages, 231 holding a table:

| | |
|---|---|
| Table pages the gate never sent to vision | **147 of 231** |
| Table row integrity, `get_text()` | **4%** of 976 |
| Same, `pymupdf4llm` | **78%** |
| Page 7 of hch6 alone | 0% → 93% |
| Space loss inside cells, the defect that remains | **15%** of 616 multi-word cells |
| Vision pages across the corpus | 103 → 51 (4.2 → 2.1 model-hours) |
| Chunks under 40 chars, after the heading fix | 19% → 10% |
| Chunks emitted | 624 → 555 |

Why the gate could not be fixed by tuning: digit ratio is a whole-page average
and losing a table is a local event. Page 7 scores 0.0377 against a 0.12
threshold because the rest of the page is prose about defrost timers. No value
of that constant finds it without dragging in every page that quotes a
temperature.

A second bug fell out. `VISION_VECTOR_DRAWINGS >= 60` was calibrated on one page
of one document (the Goodman nomenclature diagram, 202 drawings) on the
reasoning that "pages that are genuinely tables or prose have very few". Ruled
tables are drawn with vector lines: hch6's median page has **715**. So the
numeric guardrail was treating **186 table pages** as unverifiable figures and
keeping vision output it was written to reject. Table presence is now the
discriminator.

*Instrument failures worth remembering, both caught only by checking a case
whose answer was already known.* Importing `pymupdf4llm` switches on Tesseract
globally and changes `find_tables` cell text, so ground truth computed in the
same process came back as `outdoorairtempsensor` and the first run reported a
meaningless 2% vs 11%. Ground truth now comes from an isolated process. And a
scratch file named `api.py` on `sys.path` shadowed the `api` package, silently
re-running an old script instead of the import.

*What is deliberately not claimed.* No benchmark was run. Osas's call, and
correct: benchmarking a pipeline known to be broken anchors the work to a number
that measures the breakage. Extraction fidelity is a deterministic property of a
page and needs no model to check, which is a tighter signal than 22 questions at
the far end.

*The 51% that commit f362387 and its predecessor report is wrong. The figure is
78%.* Worth keeping the mistake visible, because it was a third lying instrument
in the same sprint and it hid a real defect inside a fake one.

The metric asked whether a row's first and last cell appear on one line, with
whitespace normalised to single spaces. Two things broke it:

  - A source cell containing a newline is rendered by the extractor as
    `A<br>B` inside a single markdown cell. The row is perfectly intact. The
    reference text had `A B`, so every one of those 284 rows scored as a
    failure. They pass at 2%, which should have been the tell: a number that
    extreme is a metric artifact, not a result.
  - The rest were counted as "lost rows" when the row was there and the words
    inside a cell had run together.

Comparing characters and ignoring spacing, on the same 976 rows and the same
reference data, `get_text()` scores 4% and `pymupdf4llm` scores 78%. The
direction of the sprint never changed; the size of the win was understated and
two different defects were being added together.

*Space loss inside cells: found and fixed, 2026-08-30.* The Tesseract hypothesis
was wrong; OCR flags changed nothing. `pymupdf4llm` ships two extraction paths
and defaulted to the wrong one for us. The layout path welds words when it
reassembles a cell, and the raw text layer has the spaces, so the reassembly was
losing them rather than the PDF lacking them.

|  | rows intact | spaces kept | per page |
|---|---|---|---|
| layout (was default) | 78% | 67% | 0.94s |
| **rag** (now) | **88%** | **78%** | 1.87s |

Not cosmetic: the keyword arm of `hybrid_search` tokenises on words, so
"forperformance" matches nothing anybody would type and the passage becomes
reachable only by the vector arm. Twice the time per page buys both numbers, and
ingestion is a background job that still replaces vision calls at ~146s a page.

Use `pymupdf4llm.use_layout()`, the library's own switch. Setting the private
`_use_layout` flag instead scores 85/76 rather than 88/78, because the function
rebinds other state as well as the flag.

Per document, which is what matters for where we sell:

    safe_care (dental infection control)   100% rows   96% spaces
    trane_dc                                95%        85%
    hch6                                    87%        90%
    goodman_gvxc                            86%        80%
    goodman_gszc7                           84%        69%

Dense HVAC spec tables are the hard case. Policy and procedure documents, which
is what dental, legal and accounting customers upload, are at the top of that
list. We are stress-testing on the worst corpus we own, which is the right way
round.

*Two robustness fixes found while doing it.*

`pymupdf4llm` is synchronous CPU work and was being called inline from an async
function. `get_text()` was milliseconds a page and hid the pattern; at 1.9s a
page a long manual blocks the event loop for minutes, stalling `_hold_lease`
(renews every 5 min against a 15 min expiry) and every other tenant queued
behind that worker slot. Past the lease the run is reclaimed as abandoned while
still running, and the symptom is large documents failing for no visible reason.
Now `asyncio.to_thread`, which this codebase already used in three other places.

`PyMuPDF` and `pymupdf4llm` are pinned equal and must stay that way: the latter
compares versions at import and raises `ImportError` on a mismatch. With `>=` on
both, a rebuild could produce an image where the PDF processor cannot import,
surfacing in the worker as every upload failing rather than as a bad pin.

*Untested, and not to be claimed.* Legal and accounting document shapes: scanned
contracts, tax forms, field-based forms. The evidence is one dental-adjacent PDF
and four HVAC manuals. Get real contracts or invoices and run the same
measurement before saying the pipeline is ready for those verticals.

*Also open.* The residual short chunks are bare page numbers, one occurrence
each rather than one string twenty-three times, so a rule that eats digits needs
its own measurement first. Reprocessing: none was done. Only newly ingested
documents get any of this.

## What we can honestly claim

Written 2026-08-29 after an AI-generated sales pitch was checked against the
code and roughly a third of it turned out to be false. Every false claim was a
*plausible* feature for a product like this, which is exactly why they survived
a read-through and would have failed in a meeting. Check against this before
putting capability claims in a deck, on the site, or in an email.

**True today**

| Claim | Where it lives |
|---|---|
| Permissions resolved live, per request; a connection can never exceed its creator, and dies the day they lose access | `core/auth.py` Principal, ceilings |
| One connection reaches exactly one workspace | `workspace_ceiling` |
| Only the passages that answer the question leave, never the corpus | `routes/mcp.py` |
| Documents private at rest; signed URL per request, 30 min, after an access check | `core/utils.py`, `files/access-url` |
| Every passage links to the page, and the link is a reference not a key | `/#/doc/:fileId/:page` |
| Every chunk is bound to the page and document it came from | `chunks` → `segments` → `files` |
| A superseded document stops answering, and says so if named | `superseded_by_id` |
| Says when nothing matches rather than answering from general knowledge | tool description + `_search_knowledge` |
| Flags a passage read off a figure that the text layer could not confirm | `vision_unverified_page` |
| Search cost roughly flat in library size; generation on the customer's own subscription | hybrid search + MCP |
| Nothing to install; hosted, browser consent, works beyond the CLI | `routes/oauth.py` |
| `drive.file` scope only, never keys to the whole Drive | `services/connectors.py` |
| Revocation UI showing last-used per connection | Settings → Connections |

**Not built, could be**

| Gap | Cost |
|---|---|
| Exportable per-user, per-document access log | Real work. Today: `last_used_at`, `cited_file_ids` on runs, org membership events. First thing a regulated buyer asks for. |
| Background re-sync from Drive/SharePoint | **A decision, not a task.** Requires long-lived Drive credentials, which forfeits the `drive.file` posture we sell. |
| Slack/Teams | Written, not wired. And it is a place to *ask*, never a source to index. |
| "What is indexed" tool for the connector | Small. Does not exist. |
| Wikis, chat history as sources | New connectors. |
| Document author | We store the uploader, not the author. |

**Never claim**

- **"Data stays contained in your system."** Contradicts MCP, which is the thing
  being sold. Passages go to Anthropic. Say so first, unprompted; the honest
  version is the stronger argument.
- **"Forces the AI to rely on verified records."** Nothing forces a model we do
  not run. Tool descriptions persuade.
- **"Continuously indexes Slack/Teams."** The inverse of the design.
- **"Unlimited capacity."** 100MB/file, plan-scoped document counts.
- **"Immutable."** Overclaim. "Bound to the page it came from" is true and enough.
- **Pitching this as a local/terminal developer tool.** It is hosted and works in
  the Claude apps. That framing describes an architecture we did not build and
  narrows the market to the customers least likely to pay a SaaS fee.

## Decisions not to re-litigate

One line each. The reasoning is in the commit or the code comment beside it.

**Architecture**
- Redis is a notification bus, never the queue.
- Publish after the commit, never inside it, or the worker wakes to nothing.
- Retrieval lives in SQL (`hybrid_search`), not Python.
- Ranks are fused, never scores: a cosine and a `ts_rank_cd` share no scale.
- Ingestion makes no chat call. Embeddings and vision only.
- One markdown parser, two renderers, for Word and PDF export.
- PostHog is pointed at the regional host, `us.i.posthog.com`, never at
  `app.posthog.com`. That one is the dashboard, and naming it let posthog-js
  resolve somewhere the CSP did not allow: every event blocked for 11 days
  with the product healthy and the dashboard empty. A test now reads the host
  out of the built bundle and fails if the CSP disagrees.
- Analytics events carry `environment`, decided by hostname. The local
  container serves a production build, so `import.meta.env.DEV` is false there
  and testing counted as customer behaviour.

**Credentials**
- A credential carries no permission. `workspace_api_keys` names its creator and
  one workspace, and authorization looks up what that person may do *now*, every
  request, then intersects. Copying the role onto the row would let the key
  outlive the access it was cut from; this way removing somebody from a
  workspace takes their integrations with them and there is nothing to revoke.
- Both ceilings are None for a person, which is what keeps the browser path
  byte-for-byte what it was.
- Machine-callable routes are an allowlist, enforced by
  `test_api_key_ceiling.py`, and it is load-bearing rather than tidy:
  `test_every_route_is_scoped.py` recognises eleven tenant checks and only
  `accessible_workspace_ids` has been taught the ceiling. A Principal is safe on
  a route that intersects and on no other.
- The key-management routes take `authenticate_user`, never
  `authenticate_api_caller`. A credential that can mint a credential cannot be
  revoked.
- An unknown scope grants nothing. A credential written by a newer version must
  lose what this code cannot resolve, never gain it.
- SHA-256 and not bcrypt: 256 bits of randomness has no dictionary to slow down,
  and the hash runs on every request. The comparison is still constant time.
- Hex, not base64url. `token_urlsafe` emits the `_` this format splits on.
- The rate limiter keys on the key's prefix when one is present. An integration
  calls from one server address, so per-IP would make one busy integration
  exhaust the budget for every human behind it.
- Search has never appeared in the Usage panel, for people either: that panel
  counts messages, and search writes none. So Phase 1 adds no usage table, and
  `last_used_at` is the visibility it provides. Exposing `ask` over a credential
  is what would make the panel go blind, and that is Phase 3's problem.

**The authorization server**
- One Principal, three ways to arrive at it: Firebase, an API key, an OAuth
  access token. `_RESOLVERS` in `core/auth.py` is ordered and dispatches on the
  token's tag, so adding a credential type is an entry there and nothing else.
  This is why the API-key work was not thrown away when OAuth arrived.
- S256 only. `plain` PKCE makes the challenge equal the verifier, so an
  intercepted authorization request carries everything needed to redeem its own
  code. No implicit grant and no password grant, and none of the three are
  advertised in the metadata, so a client cannot negotiate down to them.
- A replayed authorization code withdraws every token it already produced.
  Refusing the second request while leaving the first request's tokens alive
  protects nothing: a code presented twice means somebody else may have held it.
- The code is claimed in one UPDATE with `consumed_at IS NULL` in its WHERE.
  Checking and then writing leaves exactly the race this is guarding.
- Refresh tokens rotate. A stolen one is good for one use, and then the real
  client's next refresh fails loudly instead of silently sharing the grant.
- The workspace comes from the person on the consent screen, never from the
  client's request, and is checked against `accessible_workspace_ids` rather
  than trusted from the form.
- `/authorize` refuses an unregistered client or redirect with a plain error and
  never a redirect carrying one. Bouncing an error to an unverified URL is how
  an open redirect gets built by accident.
- Registration is unauthenticated because that is what dynamic client
  registration means, and it is harmless because a registered client reaches
  nothing until somebody approves a workspace for it.
- A 401 from `authenticate_api_caller` carries `WWW-Authenticate` with the
  resource-metadata URL. Without it an MCP client meeting a 401 reports
  "unauthorized" and stops, instead of starting the flow that would fix it.
- Keys and grants are separate tables numbering their rows independently, so
  revoke takes an explicit `kind`. Trying one table and falling through to the
  other revokes the wrong row: a key that stops for no reason, and an app that
  keeps working after it was cut off.
- MCP returns retrieval, not answers. Search is under a second; answering goes
  through the worker, and a tool call that stalls for ten seconds is broken in
  somebody else's chat window. The calling model does the reasoning.
- The MCP tool descriptions carry the vision caution, because they are the only
  instructions we get to give a model we do not run.

**Security and tenancy**
- Invite mail goes out with SendGrid click tracking off. On, SendGrid
  rewrites the link to `url639.syntextai.com`, our own `includeSubDomains`
  HSTS forces that to https, and SendGrid has no certificate for it: no
  invite could be accepted for four weeks. Set in code, not the dashboard,
  and held by `test_email_service.py`.
- Two questions, two functions: `accessible_workspace_ids` for may-you-see-it,
  `assert_*_capability` for may-you-do-it.
- Scope by workspace, never by uploader. Checking the uploader refused invited
  staff every document their owner added, twice, in two different places.
- A refusal is 404 when naming a resource by id.
- `test_every_route_is_scoped.py` fails on a route that reaches no tenant.
- The suite runs on its own database, `syntextai_test`, made by conftest. The
  worker polls the same queue tests enqueue into and was claiming their jobs.
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
- Deleting an account deletes every company you own, members and documents
  included: you hold the card, so your leaving ends the subscription anyway.
  Handing ownership on gives somebody a company nobody can pay for.
- The provenance note goes inside the exported file. A .docx gets emailed and
  printed, which is when everyone forgets a machine drafted it.
- Drafting does not go through `generate_explanation`: its 1500-token budget is
  right for an answer, and reasoning spends the same allowance, so a two-page SOP
  returned empty about one attempt in four.
- A supersede link points forward, so retrieval tests a column it already joined.

- `ui/` is shadcn written for React 19, running on React 18.3.1, so only the
  Overlays need `forwardRef`. Nothing else here is handed a ref.

- Firebase decides who is signed in; whether our API answers is a separate
  question. Confusing the two stranded an invited colleague, silently.

**Reading what the server sends**
- `GET /api/v1/workspaces` returns `{"items": [...]}`. The route is typed
  `Dict[str, List[WorkspaceResponse]]`, which documents the shape and not the
  key, so the key has to be read off the response rather than guessed. Reading
  `body.workspaces` showed an owner with five workspaces "create a workspace
  first", and typechecked, and passed every test: the panel had no test that
  rendered it against the real route. Found by opening the page.
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
- Contextual retrieval: ~1,500 LLM calls per 500-page manual, nothing extra.
- The slowness was the provider. Three code-level explanations were wrong first.

## Questions for Osas, not to guess at

- Are the rate limits right?
- Should Redis do more than notifications and the answer cache?
