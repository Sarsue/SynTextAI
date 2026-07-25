# SyntextAI Knowledge Assistant — PRD

Status: ready to GTM. This supersedes the generic SMB (dental/legal/accounting/home
services) positioning referenced elsewhere in the repo/marketing history. Companion
doc: `ENGINEERING_OVERVIEW.md` for how the current codebase actually works.

## One-liner

An AI assistant that lets maintenance technicians ask a repair/technical manual a
question in plain language and get the exact relevant procedure back — cited to the
source — instead of manually searching hundreds of pages.

## Problem

Technicians diagnosing an equipment issue manually cross-reference dense repair and
technical manuals — often under time pressure, often with their hands full or gloved
mid-repair. This is slow, inconsistent, and leans heavily on whichever senior
technician happens to be available. Every minute spent searching is downtime on the
equipment and the technician's time both.

## Proof point (why us, not a claim)

Before Osas Inc, Osas led an AI pilot for a Fortune 500 / government-scale aviation
client: digitizing their repair manuals into an AI-searchable system. Average
technician diagnosis search time dropped from ~30 minutes to ~20 seconds. Results
were strong enough that the client greenlit the next phase. This is real, generic
(no client named), no NDA restriction on discussing it. This is the flagship case
study and cold-email hook — not a target market (see "Explicitly out of scope").

## Target customer

- **Buyer / champion:** Maintenance Manager or Plant Maintenance Manager at a
  50–500 employee manufacturing or industrial company. Owns diagnosis time and
  downtime as a direct KPI; has enough budget authority to buy without a long
  procurement chain.
- **Economic buyer (larger deals):** Plant Manager, Operations Manager, or
  Reliability Engineer — cares about the roll-up metric (unplanned downtime,
  overall equipment effectiveness).
- **End user:** the technician on the floor, searching by symptom or fault code,
  usually hands-busy.
- **Gatekeeper (larger accounts only):** IT/procurement, mainly relevant once
  selling into larger utilities/aerospace-adjacent accounts, not at the initial
  50–500 employee target.

## Core value proposition

"Ask your manuals a question, get the exact procedure back in seconds, cited to the
source page — not a guess." Speed + trust (citation, verifiable) + fits how a
technician actually works (short symptom/fault-code queries, eventually hands-free).

## Explicitly out of scope (and why)

Decided this way after deliberately rejecting the alternatives — don't relitigate
without a real reason:

- **Not certified aviation MRO as the initial target market.** FAA-regulated
  aircraft maintenance carries liability/regulatory weight (Part 145 repair
  station rules, safety-critical certification concerns) a bootstrapped company
  shouldn't walk into first. The aviation pilot is proof of category, not the ICP.
- **Not a 5-product suite** (Enterprise Knowledge Assistant + Policy/Compliance +
  IDP + Workflow Automation + Voice, all as SyntextAI features). Those are five
  different technical stacks — building all five is Osas Inc's custom
  *Implementation* service menu (already on the Osas Inc site), not a SaaS
  product to build simultaneously. SyntextAI is #1 only.
- **Not IDP as a standalone product.** Market is dominated by IBM, Google,
  Microsoft, AWS, UiPath, Automation Anywhere — no realistic wedge for a solo
  bootstrapped entrant regardless of market size.
- **Not AI Workflow Automation as a standalone product.** Fastest-growing market
  on paper, but orchestration is being absorbed into existing enterprise
  platforms rather than bought standalone — and it doesn't match the proven
  expertise (retrieval, not agent orchestration).
- **Not invoicing/reporting-from-templates.** Different, harder, already-crowded
  problem (QuickBooks, FreshBooks, Xero); doesn't touch the actual technical
  moat (retrieval quality).
- **Not AI Receptionist bundled into the same pitch.** Different buyer (front
  office, not Maintenance Manager), different workflow. Fine as an account
  *cross-sell* after the knowledge assistant is sold — not part of the initial
  product or pitch.
- **Not a generic multimodal "agentic platform"** spanning e.g. medical video AI.
  Different regulatory universe (FDA/clinical), different buyer, different
  business entirely.

## Functional requirements

### MVP (ship first, this is what gets pitched in the cold email)
1. Upload/ingest technical manuals (PDF at minimum) into a workspace.
2. **Table- and diagram-aware ingestion.** This is the real gap vs. today's
   codebase — `api/processors/pdf_processor.py` currently OCRs image-only pages
   to plain text with no table-structure or diagram handling. Torque spec
   tables, exploded parts diagrams, and wiring schematics carry real information
   that flat-text OCR loses. Needs investigation into a table-extraction step
   (e.g. layout-aware parsing) before this vertical's content ingests reliably.
3. Natural-language + symptom/fault-code style query support. Confirm the
   current query processor performs as well on short technical queries ("error
   E-204", "won't hold pressure") as on conversational questions — this vertical
   leans much more on the former than the SOP/policy use case did.
4. Every answer cites the specific source document/section — already a strength
   of the existing pipeline (`RAGFactory` → `HybridSearchEngine` → reranker),
   carry it forward as non-negotiable, not just for trust but for liability: the
   technician verifies against the source before acting.
5. **Revision/version control.** When a manual is re-uploaded as an updated
   revision, the superseded version must drop out of retrieval — not sit
   alongside the current one. More safety-critical here than in policy search;
   an outdated torque spec is a real hazard, not just a compliance annoyance.
6. Workspace/team access (already built) — a facility's technicians share one
   workspace scoped to that facility's manuals.
7. **Explicit liability framing in the product, not just the marketing:** the
   assistant surfaces the relevant official procedure; it does not diagnose or
   decide. No UI language that implies autonomous diagnosis.

### Fast-follow (after MVP validates with real customers, not before)
8. **Voice interface** — speech-to-text in, text-to-speech out, layered onto the
   existing chat/RAG pipeline (incremental engineering, not a new product).
   Rationale: technicians are often hands-busy/gloved mid-repair — voice may be
   the difference between a tool that gets used on the floor and one that
   doesn't. Pitch this as "coming next" in the cold email/demo to create
   anticipation, but do not let it block shipping and validating the text MVP.
   **Deferred as of 2026-07-24 — not being worked on now** (see "Decided" below).
9. Usage feedback loop — thumbs up/down (or similar) on each answer. Serves two
   purposes: product improvement signal, and generates real usage data for
   future case studies (replacing the current illustrative/third-party-cited
   material on the Osas Inc site with real customer results).
10. Basic usage analytics for the Maintenance Manager (queries/week, active
    technicians, top-searched topics) — retention hook, mirrors the "search
    analytics" gap already identified as high-impact in prior competitive
    research.

### Later (only once there's real customer volume to justify it)
11. Slack/Teams integration for querying without opening a separate app.
12. Account-expansion hooks toward Osas Inc Implementation (custom integrations,
    workflow automation, voice receptionist) — sold as separate engagements to
    whoever owns that budget in the account, once SyntextAI is already the
    vendor of record.

### Removing — decided 2026-07-24
Flashcards, quiz questions, key concepts — vestigial from an earlier EdTech
pivot, not used by this vertical ("this isn't educational" — Osas). Confirmed:
remove, not leave dormant. This is a real, currently-functioning feature, not
dead code — `pdf_processor.py` generates key concepts via an LLM call on every
file upload, and `FileViewerComponent.tsx` (1,692 lines) has a dedicated
side-panel with Key Concepts / Flashcards / Quiz tabs, actively mounted in
`ChatApp.tsx` and `ConversationView.tsx`.

**Status — done 2026-07-24:**
- All 12 flashcard/quiz-question/key-concept API routes removed from
  `api/routes/files.py`, unused imports cleaned up, compiles clean.
- `FileViewerComponent.tsx` rewritten from 1,692 lines down to ~150 — kept
  `document-view-container`/`renderFileContent()` (the actual file viewer,
  confirmed needed), removed the entire `side-panel` (Key Concepts/Flashcards/
  Quiz tabs, all associated state, fetch calls, and forms). Type-checks clean
  against its callers in `ChatApp.tsx`/`ConversationView.tsx` — no prop
  changes needed. `FlashcardViewer.tsx`/`QuizInterface.tsx` are now unused but
  not deleted (harmless as orphaned files; low-priority cleanup).
- `pdf_processor.py`: removed the key-concept generation call from the
  ingestion flow, plus the three now-orphaned class methods
  (`generate_key_concepts`, `generate_learning_materials`,
  `generate_learning_materials_for_concept`) and unused imports/constant.
  **This also fixes a real latent bug**, not just cost: previously, if
  key-concept generation failed for any reason, the *entire file upload was
  marked failed* even though chunking/embedding had already succeeded.
  Removing this step means uploads can no longer fail for an unrelated
  EdTech-feature reason.
- **DB migration: not run, script provided instead of an Alembic migration.**
  Found the repo's Alembic history has multiple divergent heads and no
  tracked `alembic.ini` — rather than guess a `down_revision` against an
  already-inconsistent chain, wrote a plain reviewable SQL script at
  `docs/migrations/drop_learning_content_tables.sql` (drops `flashcards`,
  `quiz_questions`, `key_concepts`). Not applied — run manually when ready.
  The Alembic multi-head issue itself should get resolved separately, ideally
  before the engineer's first real migration.
- **Not touched:** `youtube_processor.py` and `text_processor.py` have their
  own separate copies of key-concept generation — not in scope here since PDF
  is the relevant ingestion path for this vertical. Still exists as
  unaddressed surface area if/when YouTube/text ingestion matters again.

## Non-functional requirements

- **Security fixes already identified must land before this goes to real
  customers with real proprietary manuals:** CORS is currently wide open
  (`allow_origins=["*"]`), there's no rate limiting on any endpoint (including
  the ones that trigger paid LLM/embedding calls), and several endpoints leak
  raw exception text to the client. None of these are vertical-specific, but
  they're a harder requirement now — a manufacturing client's proprietary
  repair procedures are exactly the kind of data a CORS/access-control gap
  would expose.
- Multi-tenant isolation is enforced at the application layer (no DB-level RLS)
  — every new query/endpoint touching workspace- or file-scoped data must
  filter by the authenticated user's ID/workspace at the query level. See
  `ENGINEERING_OVERVIEW.md` "Known gaps" for the pattern that was recently
  fixed and shouldn't be reintroduced.

## Success metrics

- Primary: average technician diagnosis/search time, before vs. after (mirrors
  the proof point — this is the number that sells the next customer too).
- Adoption: weekly active technicians per account, queries per technician per
  week.
- Answer quality proxy: % of answers technicians mark useful/correct (once the
  feedback loop ships).
- Business: 5 paying customers (existing Phase 1 goal), each a real
  Maintenance Manager account, before considering the pricing or vertical
  locked in.

## Pricing

Researched 2026-07-24. Comparable CMMS tools (Fiix, UpKeep, MaintainX, Limble) —
the closest direct comp — run $20–150/user/month, most landing $28–75/user/month.
Enterprise EAM platforms (IBM Maximo, SAP PM) run $15K–100K+/year per site.
General manufacturing software budgets sit at $100–300/user/month. Pure per-seat
pricing is now only 15% of the SaaS market; 61% of SaaS companies use hybrid
(base + usage) pricing, and value-based pricing captures 15–25% more revenue
than per-seat on comparable deals — worth moving toward once there's real usage
data, but don't over-engineer billing before there are paying customers.

**The real anchor is downtime cost, not the feature list.** For a general
industrial facility (the actual target — not large automotive/semiconductor
plants), unplanned downtime runs $8,000–22,000/hour. Shaving even 10–20 minutes
off a handful of diagnosis events a week is worth many multiples of any
subscription price. Lead discovery calls with this number, not with pricing —
same "what would you expect to pay for this" close already in the GTM plan.

**Recommended starting structure** — flat monthly price per facility/workspace
(matches the existing workspace-based product architecture), tiered by
technician count, positioned between CMMS entry tools and enterprise EAM:

| Tier | Price | Fit |
|---|---|---|
| Up to ~15 technicians | ~$400–600/mo | Small facility, single site |
| Up to ~50 technicians | ~$800–1,200/mo | Mid-size plant |
| Unlimited / multi-site | Custom | Larger accounts — route to Osas Inc Implementation |

This is meaningfully above the prior $49–199/mo SMB tiers and still well below
every CMMS comp and dramatically below enterprise EAM. Treat this table as the
starting point for discovery-call conversations, not a locked number — let the
first cohort's actual willingness-to-pay confirm or move it.

## GTM

- **Channel:** cold email (confirmed) — no existing warm network in
  manufacturing/industrial maintenance circles.
- **List building:** Apollo.io or LinkedIn Sales Navigator, filtered by title
  (Maintenance Manager / Plant Maintenance Manager / Reliability Engineer) and
  NAICS code for manufacturing/industrial.
- **Deliverability:** requires a warmed, properly configured sending domain
  (SPF/DKIM/DMARC), ideally separate from the primary inbox, before sending at
  volume.
- **Hook:** the 30-min-to-20-sec proof point, framed as "I built this once for
  an aviation client, now building it for manufacturing." (Draft cold email
  already written in this conversation's history — reuse and adapt.)
- **Demo:** "send me one manual, I'll show you what your team can do with it" —
  adapted from the prior SOP-based demo script to this vertical's actual
  artifact.
- **Close:** demo → discovery call → paid pilot, same motion as the existing
  Osas Inc AI Readiness Assessment funnel where it makes sense to route there
  instead (larger accounts, more customization needed).

## Decided 2026-07-24

- **Table/diagram-aware ingestion is a committed MVP requirement**, not just an
  open investigation — this is priority #1 for the engineer.
- **Voice is deferred**, not being worked on now. Not in the current build
  scope; revisit once the text MVP has real customers.
- **Flashcards/quiz/key-concepts: remove, confirmed** (see "Removing" above for
  status — partially done, frontend side panel + ingestion LLM call + DB
  migration still pending).
- **Pricing:** starting table above (see "Pricing"), to be confirmed/adjusted
  by real discovery-call conversations with the first cohort.

## Open questions (need Osas's input, not assumptions)

1. Exact technical approach for table/diagram-aware ingestion — table-structure
   extraction vs. routing pages through a vision-capable model at ingestion
   time (recommended direction, not yet decided in detail) — needs scoping
   with the engineer before committing a timeline. This is the one real
   remaining unknown blocking MVP.
2. Whether/when to run `docs/migrations/drop_learning_content_tables.sql`
   against the real database — written, not applied, needs a deliberate
   go-ahead (see "Removing" above).
3. The Alembic multi-head / missing-`alembic.ini` issue discovered while
   trying to write a proper migration — worth resolving on its own before the
   engineer needs to write a real migration for something else.
4. `youtube_processor.py`/`text_processor.py` still have their own copies of
   key-concept generation, untouched — only relevant if YouTube/text
   ingestion matters for this vertical (unlikely, but flagging it exists).
