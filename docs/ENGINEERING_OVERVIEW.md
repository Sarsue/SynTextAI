# SyntextAI: Engineering Overview

The one engineering doc. Short on purpose: what the system is, how an answer is
made, how to run it, and the few decisions worth not relearning. Detail belongs
in commit messages and in comments beside the code. Keep this under 150 lines.

## What it is

A team uploads its documents and asks questions. Every answer is cited to the
page it came from, and each citation is checked against that page before the
reader sees it. Customers are small businesses with document-heavy work. Live
and taking payments since 2026-08-03.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, async SQLAlchemy, PostgreSQL + pgvector, Alembic |
| Agents | LangGraph |
| Frontend | React + TypeScript (Vite), Firebase Auth, Stripe |
| Models | DeepInfra (OpenAI-compatible): gpt-oss-20b for chat, Qwen3-Embedding-0.6B |
| Storage | Google Cloud Storage |
| Infra | DigitalOcean droplet, nginx, Docker Compose, Redis (notifications and answer cache) |

## How an answer is made

`api/agents/answer_agent.py`, one LangGraph graph. Retrieval is agentic: the
model writes every search and judges what comes back (decided 2026-09-28).

```
research ─┬─► answer ──────────────────────────────┬─► verify ─► render
          └─► document_worker × N ─► write ─────────┘
```

1. **research**: the **coordinator** (`coordinator.py`) is a LangChain
   `create_agent` tool loop over `tools.py`: `list_documents`, `search`,
   `search_document`, `read_page`, all scoped to the asker's workspaces in SQL.
   It searches until it has the answer, then decides through a `ToolStrategy`
   tool: these passages answer it, or these documents each need a worker.
   Runs on Qwen3-235B-Instruct (`COORDINATOR_MODEL`). Limits: 5 tool calls,
   30s. Cut off, it answers from everything it read.
2. One set of passages: **answer** composes from exactly those. Several
   documents: a **document worker** (`document_worker.py`) per document, the
   same kind of agent confined to its one file, then the **writer**
   (`writer.py`) combines them and keeps every citation marker.
3. Model calls for the agents go through `ChatOpenAI` (`chat_models.py`) and
   into the same cost ledger. DeepInfra ignores "a tool call is required" for
   gpt-oss, so middleware re-asks a turn that comes back with no tool call.
4. **verify**: the **verifier** (`verifier.py`) checks each cited claim against
   the page it cites, in parallel batches of 6. A wrong page is moved to the
   right one; a claim nothing supports is marked "could not confirm".
5. **render** (`services/answer_composer.py`): markers become page links, links
   the model invented are stripped.

While it runs, the reader sees the current step with a clock and the text as
it is written (`agents/progress.py`, `StreamingAnswer.tsx`). Streamed text is
unchecked, so nothing in it is clickable and citations are plain numbers; the
final checked message replaces it. After, one collapsed line under the answer
("Checked 13 claims · 8.1s") opens into the steps: `agents/trace.py`.

## Models and cost

Each agent's model is its own setting (`api/agents/models.py`), falling back to
`MODEL_CHAT_ID`: `COORDINATOR_MODEL`, `WORKER_MODEL`, `WRITER_MODEL`,
`VERIFIER_MODEL`, plus `*_REASONING_EFFORT`. Production sets them through the
`ENV_FILE_CONTENT` GitHub secret. All are gpt-oss-20b today.

Every question and every upload records what the provider actually charged,
per model, on its run (`agent_runs.result.cost`). gpt-oss-20b: about $0.90
per 1,000 questions. Sonnet 5 on every agent measured better (22/27 citations
against 15-16/27) but costs about 50 times more, so it is not used.

## Where things live

| Path | What |
|---|---|
| `api/agents/` | The answer graph and its agents |
| `api/services/llm_service.py` | Every model call; the cost ledger |
| `api/processors/` | Ingestion: PDF (with vision for figure pages), DOCX, text |
| `api/workers/worker.py` | Job queue worker: ingest and answer runs |
| `api/routes/` | HTTP API, including `mcp.py` and `oauth.py` (Claude connector) |
| `api/evals/` | Benchmarks and measurement scripts |
| `frontend/src/` | Web app |

## Run it locally

```bash
docker compose -f docker-compose.local.yml --env-file .env.dev up --build -d
```

- **Always pass `--env-file .env.dev`.** Without it Compose reads `.env`, the
  production file, and builds the frontend with the live Stripe key.
- Drive the UI on **:5173** (dev server, has the dev sign-in harness), not
  :3000. Sign in with `window.__syntextDevSignIn(customToken)`. The harness is
  kept out of production builds three ways; `assert-no-dev-harness.mjs` fails
  the build if it ever ships.
- **"Permission denied" on every file under `/app/api`** means Colima's sshfs
  mount died: `colima stop && colima start`.
- Changing a model setting in `.env.dev` means the next `docker compose up`
  recreates the app container. Don't do that mid-benchmark.

Tests run against a real Postgres, in their own database:

```bash
docker exec -w /app syntextaiapp-local python -m pytest api/tests -q -p no:cacheprovider --rootdir /app -o asyncio_mode=auto -o asyncio_default_test_loop_scope=session -o asyncio_default_fixture_loop_scope=session
```

## Measure before believing

- `api/evals/run_benchmark.py` asks real questions through the real API and
  scores the cited pages. Two sets: `citation_benchmark.yaml` (small-business
  PDFs, 27 cited + 4 refusals) and `hvac_benchmark.yaml` (service manuals, 20).
  A single run moves by about ±3; compare several.
- `api/evals/verifier_check.py` checks the verifier's verdicts on answers
  already known. Run it before trusting any verifier change or model swap.
- Run output goes to `api/evals/results/`, which is git-ignored.
- A failing check may be the check. Twice a benchmark "regression" was the app
  container being recreated mid-run and every later question erroring.

## Ship

`develop` for all work. `master` deploys to production on push. Pushing starts
two GitHub runs: "Deploy SynTextAI" and a Dependabot scan that finishes first.
Wait for the deploy run, then confirm the live bundle changed.

## Decisions not to relearn

- Search ranks are fused, never scores: a cosine and a keyword rank share no scale.
- More passages from the same search makes answers worse (top_k 40 scored below
  25). A search aimed at a different need helps. That is why workers exist.
- Retrieval is the model's (2026-09-28), although an earlier model-chosen search
  lost to the fixed pipeline (16.2 vs 17.0). First real runs on gpt-oss-20b: it
  repeats searches and often never decides; Qwen3-235B as coordinator decided
  and delegated properly. Seen passages come back as ids, identical searches
  are not rerun, and model calls exceed tool calls so the last turn can decide.
- The verifier runs at low effort in batches: same accuracy as medium, 37s to 8s.
- Rejected after measuring: cross-encoder reranking, contextual retrieval, a
  per-document cap on search results.

## Known risks

- No database-level row security; every route must scope by workspace.
  `test_every_route_is_scoped.py` guards it.
- Rate limits (30 questions, 10 uploads per minute per IP) are a first guess.
- `nltk` has an open advisory with no fix; the affected code is never called.
- Agentic answers take 40-55s against about 10s for the fixed pipeline: each
  search round is a model call.
