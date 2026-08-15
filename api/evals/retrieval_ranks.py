"""Where each gold passage sits in the vector list, the keyword list, and the fusion.

    docker exec -w / syntextaiapp-local python /app/api/evals/retrieval_ranks.py
    docker exec -w / syntextaiapp-local python /app/api/evals/retrieval_ranks.py --sweep

The first form asks whether every source a question needs reached the context
and at what rank. The second sweeps the fusion weights against the same
question set.

WHAT IT ESTABLISHED (2026-08-06, 27 grounded questions, page-sized chunks)

    every required source in the fused top 25
      single-document   17/17
      multi-document     7/10      q16, q17, q29 miss a source outright

    benchmark score     18/27

So retrieval delivers everything roughly nine times in ten and the answer is
right two times in three: about six questions per run have what they need in
context and fail anyway. Recall is not the bottleneck, which is worth knowing
before spending a month on retrieval.

Rank tells the rest of it. Single-document gold sits at 1 to 8; the second
source of a multi-document question sits at 12, 14, 22, 24. Present, and buried
in twenty-five whole pages of prose. That is an argument for smaller retrieval
units rather than for more retrieval.

The fusion sweep found nothing to win: 0.7/0.3, which was never tuned and
survived from an era when the keyword half matched no rows at all, is the best
of eight settings. Pure vector loses one question and pure keyword loses three.

A miss today reads as "not retrieved", which lumps three different failures
together: the embedding never found it, the keyword index never found it, or
both found it and the fusion buried it. Those need different fixes, and the
fusion weights cannot be tuned sensibly without knowing which.

No model runs here. Pure SQL against the same searches hybrid_search fuses, so
it is deterministic and fast enough to sweep.

WHAT IT WAS MEASURING INSTEAD, FIXED 2026-08-15

Two ways this had drifted from the retriever it exists to describe, both of
which make an old number here incomparable with a new one:

  - the text arm ranked `s.tsv`, the SEGMENT index, while hybrid_search has
    ranked `c.tsv` since chunk-level retrieval landed on 2026-08-06. Those are
    different indexes over different units.
  - there was no literal arm at all. Production has fused three lists since
    2026-08-14, and the third one exists because it is what puts a rare token
    like 251 or E4 at the top. Sweeping vector against keyword while the
    shipped retriever fuses three could not have described it.
"""
import asyncio, os, statistics, sys
import yaml
from sqlalchemy import text
from api.repositories.repository_manager import RepositoryManager
from api.repositories.async_file_repository import literal_tokens
from api.services.llm_service import get_text_embedding, aclose_client

WS = int(os.getenv("WS", "4219"))
POOL = 100
# The shipped weights, so the default run describes what customers get.
VW, BW, LW = 0.7, 0.3, 0.7

VEC = """
SELECT c.id, s.page_number, f.file_name,
       ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:emb AS vector)) AS rank
FROM chunks c JOIN files f ON f.id=c.file_id LEFT JOIN segments s ON s.id=c.segment_id
WHERE f.workspace_id=:w ORDER BY c.embedding <=> CAST(:emb AS vector) LIMIT :pool
"""

TXT = """
WITH q AS (SELECT REPLACE(plainto_tsquery('english', :kw)::text,' & ',' | ')::tsquery AS kw)
SELECT c.id, s.page_number, f.file_name,
       ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, q.kw, 32) DESC) AS rank
FROM chunks c JOIN files f ON f.id=c.file_id LEFT JOIN segments s ON s.id=c.segment_id
CROSS JOIN q
WHERE f.workspace_id=:w AND c.tsv @@ q.kw
ORDER BY ts_rank_cd(c.tsv, q.kw, 32) DESC LIMIT :pool
"""

LIT = """
WITH q AS (SELECT CAST(:lit AS tsquery) AS lit)
SELECT c.id, s.page_number, f.file_name,
       ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, q.lit, 32) DESC) AS rank
FROM chunks c JOIN files f ON f.id=c.file_id LEFT JOIN segments s ON s.id=c.segment_id
CROSS JOIN q
WHERE f.workspace_id=:w AND c.tsv @@ q.lit
ORDER BY ts_rank_cd(c.tsv, q.lit, 32) DESC LIMIT :pool
"""


def _best_rank_per_page(rows):
    """A page is the citation unit, so several chunks of it collapse to its best
    rank. The rows arrive in rank order, so the first one seen is the best."""
    out = {}
    for _cid, page, fname, rank in rows:
        out.setdefault((fname, page), int(rank))
    return out


async def ranks_for(session, question, emb):
    vec = (await session.execute(text(VEC), {"emb": emb, "w": WS, "pool": POOL})).all()
    txt = (await session.execute(text(TXT), {"kw": question, "w": WS, "pool": POOL})).all()

    tokens = literal_tokens(question)
    lit = []
    if tokens:
        lit = (await session.execute(
            text(LIT), {"lit": " | ".join(tokens), "w": WS, "pool": POOL}
        )).all()

    return _best_rank_per_page(vec), _best_rank_per_page(txt), _best_rank_per_page(lit)


def fuse(v, t, lit, vw, bw, top, lw=LW):
    scores = {}
    for ranks, weight in ((v, vw), (t, bw), (lit, lw)):
        for key, rank in ranks.items():
            scores[key] = scores.get(key, 0.0) + weight / (60 + rank)
    ordered = sorted(scores, key=scores.get, reverse=True)
    return {key: i for i, key in enumerate(ordered[:top], start=1)}


async def main():
    sweep = "--sweep" in sys.argv
    bench = yaml.safe_load(open("/app/api/evals/citation_benchmark.yaml"))
    qs = [q for q in bench["questions"] if q.get("citations")]
    repo = RepositoryManager().file_repo

    cache = []
    async with repo.get_async_session() as session:
        for q in qs:
            emb = "[" + ",".join(str(float(x)) for x in await get_text_embedding(q["question"])) + "]"
            v, t, l = await ranks_for(session, q["question"], emb)
            gold = [(c["file"], p) for c in q["citations"] for p in c["pages"]]
            cache.append((q, v, t, l, gold))

    if sweep:
        # The binding constraint is the WORST required source, not the easiest.
        # A multi-document question whose second source sits at rank 24 has
        # technically retrieved it and practically buried it.
        multi = [c for c in cache if len(c[0]["citations"]) > 1]
        single = [c for c in cache if len(c[0]["citations"]) == 1]

        def measure(rows, vw, bw, top=25):
            complete, worst = 0, []
            for q, v, t, l, _g in rows:
                # The literal arm is held at its shipped weight through the
                # sweep. It answers a different question from the other two --
                # "which chunk contains this exact token" -- and folding it into
                # a vector-against-keyword trade-off would make both axes mean
                # something else.
                fused = fuse(v, t, l, vw, bw, top)
                per = [next((fused.get((c["file"], p)) for p in c["pages"]
                             if (c["file"], p) in fused), None) for c in q["citations"]]
                if all(per):
                    complete += 1
                    worst.append(max(per))
            return complete, (statistics.mean(worst) if worst else 0)

        print(f"{'vec/kw':>9}{'single 17':>11}{'multi 10':>10}{'all 27':>9}"
              f"{'mean worst rank':>17}")
        for vw in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.3, 0.0):
            bw = round(1.0 - vw, 1)
            sc, _ = measure(single, vw, bw)
            mc, mw = measure(multi, vw, bw)
            ac, aw = measure(cache, vw, bw)
            star = "  <- shipped" if vw == 0.7 else ""
            print(f"{f'{vw}/{bw}':>9}{sc:>11}{mc:>10}{ac:>9}{aw:>17.1f}{star}")
        await aclose_client()
        return

    # A multi-document question needs EVERY required source in the context,
    # not the easiest one. Scoring the best gold page hides exactly the failure
    # the loop was built for.
    full, partial = 0, 0
    print(f"{'q':>3} {'srcs':>5} {'in top25':>9}  {'fused ranks':<22} question")
    for q, v, t, l, gold in cache:
        fused = fuse(v, t, l, VW, BW, 25)
        per_source = []
        for c in q["citations"]:
            hit = next((fused.get((c["file"], p)) for p in c["pages"]
                        if (c["file"], p) in fused), None)
            per_source.append(hit)
        got = sum(1 for h in per_source if h)
        if got == len(per_source):
            full += 1
        elif got:
            partial += 1
        marks = ", ".join(str(h) if h else "MISSING" for h in per_source)
        flag = "" if got == len(per_source) else "   <-"
        print(f"{q['id']:>3} {len(per_source):>5} {f'{got}/{len(per_source)}':>9}  {marks:<22} {q['question'][:34]}{flag}")

    multi = [c for c in cache if len(c[0]["citations"]) > 1]
    single = [c for c in cache if len(c[0]["citations"]) == 1]
    def rate(rows):
        ok = 0
        for q, v, t, l, gold in rows:
            fused = fuse(v, t, l, VW, BW, 25)
            if all(any((c["file"], p) in fused for p in c["pages"]) for c in q["citations"]):
                ok += 1
        return ok, len(rows)
    print(f"\n  every required source in the top 25")
    print(f"    single-document  {rate(single)[0]}/{rate(single)[1]}")
    print(f"    multi-document   {rate(multi)[0]}/{rate(multi)[1]}")
    await aclose_client()

asyncio.run(main())
