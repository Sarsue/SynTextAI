"""The tools a model may use to answer from a customer's documents.

WHY THIS EXISTS

The pipeline this replaces made every decision up front: one search, with one
phrasing of the question, and whatever came back was the answer's only evidence.
That works for "how long do i keep tax records" and cannot work for "what do i
need to do to keep employees safe and my premises accessible", which is two
searches in two documents. It also has nowhere to put the judgement "none of
this actually answers the question", so asked how to register a trademark it
produced a confident walkthrough of a database it had never seen.

A tool lets the model look, read what came back, and look again. That is the
whole point: retrieval stops being a fixed step and becomes something the model
does as many times as the question needs.

TENANCY

Read this before adding a tool.

The model never names a workspace. Scope is bound once, here, from the
authenticated request, and the schemas the model sees carry no workspace or user
argument at all. A model cannot ask for another company's documents because
there is no field in which to ask, and prompt injection inside an uploaded PDF
has nothing to inject into. Every tool goes through the same repository calls
the HTTP routes use, with the same accessible_workspace_ids scoping, so a tool
can never see more than the signed-in user could see by clicking.

`read_page` takes a file NAME rather than an id for the same reason. Ids are
guessable and belong to a global sequence; a name is resolved against the files
this caller can already list.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many passages one search returns. The fixed pipeline puts 25 in front of
# the model in a single shot; at 8 this agent was seeing a third of that per
# call, which is a plausible reason it cited the first page matching a phrase
# rather than the page defining the concept. Tunable so the question can be
# settled by running it rather than argued about.
MAX_SEARCH_RESULTS = int(os.getenv("TOOL_SEARCH_RESULTS", "8"))
MAX_PAGE_CHARS = 6000

# What happens to a passage between being found and being cited. Every drop is
# recorded with a reason, so a regression lands in exactly one place instead of
# showing up as "the score went down".
#
# Today only the first and last are reachable. The middle stages exist because
# the selector is about to be built, and without them a change that turns
# "cited the wrong page" into "rejected the right page" would look like an
# improvement: both leave the page uncited and the score identical.
STAGES = ("retrieved", "clustered", "verified", "selected", "used", "cited")


def _best_per_page(items) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for e in items:
        key = f"{e['file_name']} p{e['page_number']}"
        stage = e.get("stage") or "retrieved"
        prev = out.get(key)
        if prev is None or STAGES.index(stage) > STAGES.index(prev["stage"]):
            out[key] = {"stage": stage, "why": e.get("rejected_because")}
    return out


def _cite_header(file_name: str, page: Any) -> str:
    """The heading above every passage, carrying the citation to copy.

    It used to read "--- osha_small_business_handbook.pdf, page 15 ---" and the
    model would helpfully render that as "OSHA handbook, page 15", which no
    longer names a file and so cannot be resolved to a link. Every such citation
    was dropped, and answers built on the right pages arrived with none. Spell
    out the exact string to copy, next to the text it belongs to.
    """
    return (
        f"--- {file_name}, page {page} ---\n"
        f"[cite this passage as: ({file_name}, page {page})]"
    )


# The schemas the model sees. No workspace_id, no user_id, no file_id: see the
# tenancy note above.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the customer's documents and return the most relevant "
                "passages, each with its file name and page number. Expect to "
                "call this several times for one question: a narrow search "
                "repeated beats one broad search, because a single ranking puts "
                "one part of a question above the rest and drops the others. "
                "Search each part separately, and search again for any rule the "
                "first results used without defining. If the results do not "
                "answer the question, say so instead of answering from your own "
                "knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look for, in the words the documents would "
                            "use. 'self-inspection checklist' finds more than "
                            "'how do i check my workplace'."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read one full page of one document. Use it when a search result "
                "uses a term or a rule without defining it: the definition is "
                "usually a page or two earlier, and that earlier page is the one "
                "worth citing. Also use it to check a figure or a deadline "
                "before stating it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Exactly as it appears in search results or list_documents.",
                    },
                    "page": {"type": "integer", "description": "Page number, starting at 1."},
                },
                "required": ["file_name", "page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "List the documents available. Use it to find out whether a "
                "subject is covered at all before concluding it is missing."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outline",
            "description": (
                "Show a document's table of contents: its section headings and "
                "the page each one starts on. This is how to find where a rule "
                "is DEFINED rather than where it happens to be mentioned. If a "
                "search result uses a term without defining it, look here for "
                "the section that names it, then read_page that page and cite "
                "it. Prefer this over guessing more search phrasings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Exactly as it appears in list_documents or a search result.",
                    }
                },
                "required": ["file_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_within",
            "description": (
                "Search inside one named document. Use it once you know which "
                "document should hold the answer, so results are not crowded "
                "out by a longer document that merely uses the same words."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {"type": "string", "description": "Exactly as listed."},
                    "query": {"type": "string", "description": "What to look for."},
                },
                "required": ["file_name", "query"],
            },
        },
    },
]


class DocumentTools:
    """Executes tool calls against one caller's documents and nothing else."""

    def __init__(
        self,
        *,
        store: Any,
        user_id: int,
        workspace_id: Optional[int] = None,
        accessible_workspace_ids: Optional[List[int]] = None,
        file_id: Optional[int] = None,
    ):
        self._store = store
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._accessible_workspace_ids = accessible_workspace_ids
        self._file_id = file_id
        # Every passage the model was shown, keyed by (file_name, page), so the
        # answer can be turned into citations the customer can click. The model
        # reporting which page it used is not evidence that it read that page;
        # this is the record of what it was actually given.
        self.seen: Dict[tuple, Dict[str, Any]] = {}
        # What the model asked for, in order, and what came back. Kept because
        # a final answer cannot distinguish between the four ways this loop
        # fails: a bad query, good retrieval the answer then ignored, stopping
        # too early, or searching the same thing three times. Three separate
        # wrong conclusions were drawn today from having only the answer to
        # look at.
        self.trace: List[Dict[str, Any]] = []
        self._recorded_pages: set = set()
        # The evidence set: every passage retrieved, with a score accumulated
        # across every search that returned it.
        #
        # This is the thing the agent was missing. The fixed pipeline hands the
        # model ONE globally ranked list, and that ordering is information the
        # model uses. The agent called the same search more times and then threw
        # the ordering away: each search returned its own top eight, ranked
        # within itself, and nothing related search one's third result to search
        # four's first. Asked what to track for taxes and travel, it had both
        # required pages and cited four of their neighbours instead.
        #
        # It also explains two results that looked unrelated: raising results
        # per search from 8 to 20 made things worse, and searching more often
        # changed nothing. More unranked material either way.
        self.evidence: Dict[tuple, Dict[str, Any]] = {}

    def segments_on_page(self, file_name: str, page: int) -> List[Dict[str, Any]]:
        """Every segment stored for one page of one document.

        A page is not one passage. IRS 334 page 14 is two segments, one about
        tax years and one about cash versus accrual, and keying evidence on
        (file, page) let the second silently overwrite the first. Which one
        survived depended on retrieval order, so the same question could return
        different text on different runs and look like model nondeterminism.
        """
        return [
            e for e in self.evidence.values()
            if e["file_name"] == file_name and e["page_number"] == page
        ]

    def _add_evidence(self, key: Any, rank: int, chunk: Dict[str, Any], query: str) -> int:
        """Fold one search result into the evidence set. Returns its global rank.

        Scored by reciprocal rank, summed over the searches that found it. Same
        principle the SQL uses to fuse the vector and keyword lists, applied one
        level up to fuse the agent's own searches. A page that two different
        queries both rank highly is stronger evidence than one that a single
        query happened to return, and summing is what says so.

        Raw scores could not be compared across searches: each query produces
        its own distribution. Ranks can.
        """
        item = self.evidence.get(key)
        if item is None:
            item = {
                "file_name": chunk.get("file_name"), "page_number": chunk.get("page_number"),
                "file_url": chunk.get("file_url"),
                "content": (chunk.get("content") or "").strip(),
                "score": 0.0, "hits": 0, "queries": [],
            }
            self.evidence[key] = item
        item["score"] += 1.0 / (60 + rank)
        item.setdefault("stage", "retrieved")
        item.setdefault("rejected_because", None)
        item["hits"] += 1
        if query and query not in item["queries"]:
            item["queries"].append(query)
        return self.rank_of(key)

    def clustered_evidence(self, limit: int = 25) -> List[Dict[str, Any]]:
        """The evidence set grouped into runs of consecutive pages.

        A question about record retention pulls back IRS 583 pages 11, 12, 13
        and 14, all from the same section, and the model spends its judgement
        choosing between four near-identical candidates instead of choosing
        between three documents. Grouping them turns that into one decision.

        Pages keep their own identity inside the group. They have to: a citation
        is a page, because a page is what the reader opens, and two benchmark
        questions legitimately need adjacent pages rather than one of them.
        Collapsing a run into a single citable unit would trade a selection
        problem for a granularity problem.
        """
        items = self.ranked_evidence(limit)
        by_file: Dict[str, List[Dict[str, Any]]] = {}
        for e in items:
            by_file.setdefault(e["file_name"], []).append(e)

        groups: List[Dict[str, Any]] = []
        for file_name, pages in by_file.items():
            pages.sort(key=lambda e: e["page_number"] or 0)
            run: List[Dict[str, Any]] = []
            for e in pages:
                if run and (e["page_number"] or 0) - (run[-1]["page_number"] or 0) > 1:
                    groups.append({"file_name": file_name, "pages": run})
                    run = []
                run.append(e)
            if run:
                groups.append({"file_name": file_name, "pages": run})

        # Strongest group first, by its best page, so the global ordering the
        # answer step depends on survives the grouping.
        groups.sort(key=lambda g: max(e["score"] for e in g["pages"]), reverse=True)
        return groups

    def render_evidence(self, header: str = "Evidence so far", limit: int = 20) -> str:
        """The evidence set as the model should read it: ranked, once."""
        items = self.ranked_evidence(limit)
        if not items:
            return "No passages found yet."
        out = [f"{header}. {len(self.evidence)} passages found, best first."]
        for i, e in enumerate(items, start=1):
            found_by = (
                f"  [matched {e['hits']} of your searches]" if e["hits"] > 1 else ""
            )
            out.append(
                f"\n--- {i}. {e['file_name']}, page {e['page_number']}{found_by} ---\n"
                f"[cite this passage as: ({e['file_name']}, page {e['page_number']})]\n"
                f"{e['content']}"
            )
        return "\n".join(out)

    def advance(self, key: tuple, stage: str) -> None:
        """Record that a passage got as far as `stage`."""
        item = self.evidence.get(key)
        if item is None or stage not in STAGES:
            return
        if STAGES.index(stage) > STAGES.index(item.get("stage") or "retrieved"):
            item["stage"] = stage

    def reject(self, key: tuple, reason: str) -> None:
        """Record that a passage was dropped, and why.

        The reason is the point. "Rejected" tells you a page did not make it;
        "rejected: no verifiable quote" and "rejected: duplicate of p12" and
        "rejected: answers no information need" are three different bugs.
        """
        item = self.evidence.get(key)
        if item is not None:
            item["rejected_because"] = reason

    def lifecycle(self) -> Dict[str, Any]:
        """How far each passage got, and why the rest stopped."""
        reached: Dict[str, int] = {st: 0 for st in STAGES}
        reasons: Dict[str, int] = {}
        for e in self.evidence.values():
            stage = e.get("stage") or "retrieved"
            # Counted cumulatively: something that was cited was also selected.
            for st in STAGES[: STAGES.index(stage) + 1]:
                reached[st] += 1
            why = e.get("rejected_because")
            if why:
                reasons[why] = reasons.get(why, 0) + 1
        return {
            "reached": reached,
            "rejected_because": reasons,
            # Per page, not just totals. Counts say 83% was discarded; they
            # cannot say whether the page that answers the question was in the
            # 83%. Since the benchmark knows the right page for every question,
            # this turns every miss into exactly one bucket: never retrieved,
            # retrieved then rejected, selected then unused, or used wrongly.
            # Keyed by page because that is what a citation names and what the
            # benchmark's gold answers are written in. A page holding two
            # segments reports the furthest either of them got, so a page counts
            # as cited when any of its segments was.
            "pages": _best_per_page(self.evidence.values()),
        }

    def rank_of(self, key: Any) -> int:
        ordered = sorted(self.evidence.items(), key=lambda kv: kv[1]["score"], reverse=True)
        for i, (k, _e) in enumerate(ordered, start=1):
            if k == key:
                return i
        return len(ordered)

    def ranked_evidence(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Everything found so far, best first. One list, one ordering."""
        return sorted(
            self.evidence.values(), key=lambda e: e["score"], reverse=True
        )[:limit]

    def _record(self, tool: str, args: Dict[str, Any], pages: List[tuple], note: str = "") -> None:
        before = len(self.seen)
        new = [p for p in pages if p not in self._recorded_pages]
        self._recorded_pages.update(pages)
        self.trace.append({
            "step": len(self.trace) + 1,
            "tool": tool,
            "args": {k: (str(v)[:120]) for k, v in (args or {}).items()},
            "returned": [f"{f} p{pg}" for f, pg in pages],
            # New pages are the point. A search that returns eight passages the
            # model has already seen has cost a round trip and taught it
            # nothing, and that is invisible in a count of searches.
            "new_pages": [f"{f} p{pg}" for f, pg in new],
            "note": note,
        })

    async def run(self, name: str, arguments: str | Dict[str, Any]) -> str:
        """Run one tool call and return its result as text for the model.

        Never raises. A tool that fails returns a sentence saying so, because a
        model that reads "no results" can recover by searching differently,
        while an exception ends the turn and the customer gets nothing.
        """
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError:
            return "Could not read the arguments for that call. Send valid JSON."

        try:
            if name == "search_documents":
                return await self._search(str(args.get("query") or "").strip())
            if name == "read_page":
                return await self._read_page(
                    str(args.get("file_name") or "").strip(), args.get("page")
                )
            if name == "list_documents":
                return await self._list_documents()
            if name == "outline":
                return await self._outline(str(args.get("file_name") or "").strip())
            if name == "search_within":
                return await self._search(
                    str(args.get("query") or "").strip(),
                    only_file=str(args.get("file_name") or "").strip(),
                )
        except Exception as e:
            logger.error(f"tool {name} failed: {e}", exc_info=True)
            return f"The {name} tool failed. Try a different approach."

        return f"There is no tool called {name}."

    async def _resolve(self, file_name: str):
        """A file the caller can already see, found by name. None otherwise.

        By name rather than by id: ids are guessable and belong to a global
        sequence, a name is only meaningful against this caller's own listing.
        """
        listing = await self._files()
        return next(
            (f for f in listing if (f.get("file_name") or "").lower() == file_name.lower()),
            None,
        )

    async def _outline(self, file_name: str) -> str:
        if not file_name:
            return "outline needs a file_name."
        match = await self._resolve(file_name)
        if not match:
            available = ", ".join(sorted({f.get("file_name") or "" for f in await self._files()}))
            return f"There is no document called {file_name}. Available: {available or 'none'}"
        entries = await self._store.file_repo.get_outline(match["id"])
        self._record("outline", {"file_name": file_name}, [], f"{len(entries)} headings")
        from api.services.outline import render
        return render(entries, match.get("file_name") or file_name)

    async def _search(self, query: str, only_file: str = "") -> str:
        if not query:
            return "A search needs a query."

        from api.services.llm_service import get_text_embedding

        scope_file_id = self._file_id
        if only_file:
            match = await self._resolve(only_file)
            if not match:
                return f"There is no document called {only_file}."
            scope_file_id = match["id"]

        chunks = await self._store.file_repo.hybrid_search(
            user_id=self._user_id,
            query=query,
            query_embedding=await get_text_embedding(query),
            workspace_id=self._workspace_id,
            file_id=scope_file_id,
            top_k=MAX_SEARCH_RESULTS,
            accessible_workspace_ids=self._accessible_workspace_ids,
        )
        tool_name = "search_within" if only_file else "search_documents"
        if not chunks:
            self._record(tool_name, {"query": query, "file_name": only_file}, [], "no matches")
            return "No passages matched that query."

        pages: List[tuple] = []
        for rank, c in enumerate(chunks, start=1):
            file_name = c.get("file_name") or "unknown"
            page = c.get("page_number")
            self._remember(file_name, page, c.get("file_url"))
            # Identity is the segment. Two segments can share a page, and
            # keying on the page threw one of them away.
            self._add_evidence(
                c.get("segment_id") or c.get("chunk_id") or (file_name, page, rank),
                rank, c, query,
            )
            pages.append((file_name, page))

        self._record(tool_name, {"query": query, "file_name": only_file}, pages)

        # Return the whole evidence set in its global order, not this search's
        # eight in theirs. The model asked one more question; what it gets back
        # is everything known so far, best first, so a page found by two
        # searches sits above one found by neither.
        return self.render_evidence(
            header=f"Evidence after searching for \"{query}\""
                   + (f" in {only_file}" if only_file else "")
        )

    async def _read_page(self, file_name: str, page: Any) -> str:
        if not file_name:
            return "read_page needs a file_name."
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            return "read_page needs a whole number for page."

        match = await self._resolve(file_name)
        if not match:
            available = ", ".join(sorted({f.get("file_name") or "" for f in await self._files()}))
            return f"There is no document called {file_name}. Available: {available or 'none'}"

        segments = await self._store.file_repo.get_segments_for_page(match["id"], page_num)
        if not segments:
            return f"{file_name} has no page {page_num}."

        page_key = (match.get("file_name"), page_num)
        self._remember(match.get("file_name"), page_num, match.get("file_url"))
        # A page the model chose to open deliberately is strong evidence, so it
        # enters the set as though it were a top-ranked search hit. One entry
        # per segment, so a two-segment page contributes both.
        for seg in segments:
            self._add_evidence(
                seg.get("id") or f"page:{match.get('file_name')}:{page_num}",
                1,
                {"file_name": match.get("file_name"), "page_number": page_num,
                 "file_url": match.get("file_url"),
                 "content": (seg.get("content") or "").strip()},
                f"read_page {file_name} p{page_num}",
            )
        self._record("read_page", {"file_name": file_name, "page": page_num}, [page_key])
        body = "\n".join((s.get("content") or "").strip() for s in segments)
        header = _cite_header(match.get("file_name"), page_num)
        return f"{header}\n{body[:MAX_PAGE_CHARS]}"

    async def workspace_map(self, max_chars: int = 12000) -> str:
        """Every document and its contents page, ready to put in a prompt.

        The `outline` tool exists and the model does not call it, exactly as it
        did not call read_page when told to. Two prompt revisions failed to
        change that, so this stops asking. At SMB scale the whole navigation
        index is small enough to hand over: five documents and 281 headings is
        about four thousand tokens against a window of a hundred and twenty
        thousand. A model that can already see where things are does not have to
        decide to go and look.

        Bounded, because a customer with four hundred documents is a different
        problem, and there the model has list_documents and outline to work
        with instead.
        """
        from api.services.outline import render

        listing = await self._files()
        if not listing:
            return ""

        parts: List[str] = []
        used = 0
        for f in listing:
            name = f.get("file_name") or ""
            entries = await self._store.file_repo.get_outline(f["id"])
            block = render(entries, name) if entries else f"{name} (no contents page)"
            if used + len(block) > max_chars:
                parts.append(
                    f"...and {len(listing) - len(parts)} more documents. "
                    "Use list_documents and outline to see them."
                )
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    async def _list_documents(self) -> str:
        listing = await self._files()
        self._record("list_documents", {}, [], f"{len(listing)} documents")
        if not listing:
            return "There are no documents in this workspace."
        return "\n".join(f"- {f.get('file_name')}" for f in listing)

    async def _files(self) -> List[Dict[str, Any]]:
        result = await self._store.file_repo.get_files_for_user(
            user_id=self._user_id,
            skip=0,
            limit=100,
            workspace_id=self._workspace_id,
            accessible_workspace_ids=self._accessible_workspace_ids,
        )
        if isinstance(result, dict):
            return result.get("files") or result.get("items") or []
        return result or []

    def _remember(self, file_name: Optional[str], page: Any, file_url: Optional[str]) -> None:
        if not file_name or page is None:
            return
        self.seen.setdefault(
            (file_name, int(page)),
            {"file_name": file_name, "page_number": int(page), "file_url": file_url},
        )
