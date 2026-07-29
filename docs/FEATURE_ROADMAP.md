# SyntextAI — Feature Roadmap

Prioritized backlog, not a wishlist. Sourced from `ENGINEERING_OVERVIEW.md`'s
known gaps and roadmap, `CAPABILITY_GAP_ANALYSIS.md`'s findings, and prior
"must fix before first customer" tracking, consolidated into one place.
Update this as items close or new ones turn up, same as the gap-analysis doc.

## Tier 0 — Blocking, not features, fix before a real customer touches this

Bugs and security gaps, not new capability. Nothing below this tier should
get prioritized over these.

1. **Alembic migration for `workspace_members`/`workspace_invites` not run
   on the live DB.** Written, not applied.
2. **Pending invite lost after login** — `Auth.tsx` doesn't read
   `sessionStorage.pending_invite_token` post sign-in.
3. **Staff role enforcement missing on the backend** — roles exist in the DB,
   nothing stops staff from uploading (owner-only action) via a direct API
   call.
4. **Stripe end-to-end not confirmed** — trial → active → redirect flow
   needs a real run-through on the live environment, not just locally.
5. **CORS wide open** (`allow_origins=["*"]` with `allow_credentials=True`
   in `api/app.py`). Needs locking to the actual frontend domain(s).
6. **No rate limiting anywhere** — chat and file upload trigger paid
   LLM/embedding calls with zero per-user or per-IP throttle.
7. **13+ endpoints leak raw exception text** to the client instead of a
   generic message with the real detail logged server-side.
8. **No security headers** (CSP, HSTS, X-Frame-Options) at the app or nginx
   layer. Matters more than average here, healthcare and legal verticals
   are exactly the buyers who'll ask.

## Tier 1 — Cheap, closes an existing gap

9. **Wire `TextProcessor` into `factory.py`** for `.txt`/`.md` support —
   already built (`api/processors/text_processor.py`), never imported
   anywhere, orphaned. Confirm it actually works end to end first, it's
   never been exercised through the factory path.
10. **Demo workspace** — sample documents + example Q&A, needed for sales
    demos and the "send me one SOP, I'll show you" pitch to actually work
    live.
11. **Clean up orphaned EdTech code** — `generate_mcq_from_key_concepts`
    and related functions in `api/workflows/tasks.py`, unreachable since
    the routes calling them were removed, but never deleted themselves.
    Low priority, cheap when touching that file anyway.

## Tier 2 — Phase 2, stickiness (daily use, retention)

Goal per `ENGINEERING_OVERVIEW.md`: daily use, churn under 5%.

12. **Google Drive / SharePoint sync** — biggest lift here, also the
    biggest "no setup project" promise-keeper.
13. **Slack/Teams/WhatsApp bot** — staff ask questions without opening a
    browser. Flagged previously as the highest-impact SMB retention hook.
14. **Activity history**
15. **Answer feedback** (thumbs up/down or similar) — doubles as real usage
    data for case studies, replacing the current illustrative one.
16. **Admin dashboard with search analytics** — lets an owner see what
    their team is actually searching for. Also previously identified as a
    high-impact retention hook competitors don't offer.
17. **Saved prompts**

## Tier 3 — Phase 3, automation (this is where "workflow automation" becomes real)

Goal: search → action. This tier is what would make Osas Inc's "workflow
automation" claim actually true for SyntextAI specifically, not just for
custom Implementation work. Don't market it before it's here.

18. AI-generated SOPs
19. Meeting summaries
20. Onboarding assistant
21. Proposal drafting
22. Approval workflows

## Tier 4 — Phase 4, AI operating layer

Only plan for this once there are 50+ customers, don't build ahead of it.

23. CRM/email integrations
24. Cross-chat memory
25. Audit logs
26. API access

## Explicitly not being built right now

- **Manufacturing-specific ingestion** (table/diagram-aware PDF parsing,
  symptom/fault-code query tuning, revision control) — was the PRD's MVP
  priority for a manufacturing pivot that got shelved in favor of staying
  broad SMB (see `project_syntextai.md` memory). Revisit only if a real
  manufacturing customer materializes through the Manufacturing vertical
  tab, don't build ahead of that demand.
- **Standalone AI search UI** — the retrieval engine is real, but a
  dedicated search surface separate from chat isn't planned. Revisit if a
  customer specifically asks for it.
- **Voice interface** — deferred per the PRD, not in current scope.
