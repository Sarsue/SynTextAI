"""Answering by letting the model search, instead of searching for it.

The pipeline beside this one retrieves once, with one phrasing, and hands the
model whatever came back. This one gives the model the search and lets it decide
how many times to call it and with what words.

That difference is not architectural taste. It is aimed at the failures the
fixed pipeline cannot express:

  a question with two halves      "keep employees safe AND premises accessible"
                                  is two searches in two documents; one ranked
                                  list has to serve both and does not.

  a question the documents        asked how to register a trademark, the fixed
  do not answer                   pipeline gets twenty-five pages of adjacent
                                  material and no way to conclude "none of this
                                  is about trademarks", so it wrote a confident
                                  walkthrough of a database it had never seen.

  choosing between sources        two documents both cover cash versus accrual;
                                  which to cite is a judgement made after seeing
                                  results, and a ranking function has nowhere to
                                  put it.

WHAT THE MODEL IS NOT TRUSTED WITH

Citations. The model says which page it used; this module checks that claim
against the passages the tools actually returned, and drops any citation to a
page the model was never shown. A model reporting a page number is not evidence
that the page exists.

Scope. See document_tools: the model cannot name a workspace, so it cannot ask
for another company's documents.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from api.agents.document_tools import TOOL_SCHEMAS, DocumentTools
from api.services.llm_service import (
    MAX_TOKENS_CONTEXT,
    chat_with_tools,
    generate_explanation,
)

logger = logging.getLogger(__name__)

# Enough turns to consult the contents pages, search a document or two, read a
# page to check a figure, and answer. Raised from 6 after the workspace map
# went in: with somewhere to navigate, questions spanning two documents began
# running out of turns mid-answer and returning nothing.
# Unbounded loops are how an agent turns one question into a bill.
MAX_TURNS = int(os.getenv("TOOL_MAX_TURNS", "8"))

# How many passages the answer step sees. The pipeline shows 25 in one
# ranked block and scores 17.0; this is the agent's equivalent, and the
# ordering is the part that matters rather than the number.
EVIDENCE_LIMIT = int(os.getenv("TOOL_EVIDENCE_LIMIT", "25"))

# Tries to get structured output from the classifier before giving up on it.
SELECTOR_ATTEMPTS = int(os.getenv("TOOL_SELECTOR_ATTEMPTS", "3"))

# Off in production, on when measuring. See the strict branch below for why a
# silent fallback and an experiment do not belong in the same run.
SELECTOR_STRICT = os.getenv("TOOL_SELECTOR_STRICT", "false").lower() == "true"

SYSTEM_PROMPT = """You answer questions about a specific company's own documents.

You cannot see any document until you search for it. Use search_documents.

Rules, in order of importance:

1. Cite by copying, exactly, the line each passage carries:

       [cite this passage as: (osha_small_business_handbook.pdf, page 15)]

   so your answer reads "...within 8 hours (osha_small_business_handbook.pdf,
   page 15)". Use the full file name with its extension, every time, even when
   it is long and even when you have already named the document in the sentence.
   "OSHA handbook, page 15" and "Publication 583, page 12" are NOT citations:
   they name no file, so they cannot be turned into a link the reader can
   follow, and they are discarded. Never guess a page number.

2. Answer only from what the tools return. Never from your own knowledge. If
   the tools return nothing that answers the question, say plainly that the
   documents do not cover it, and name what they do cover. Being about the same
   broad subject is not enough: passages about running a small business do not
   answer a question about registering a trademark.

3. Search more than once, narrowly, rather than once broadly. One search that
   combines every part of a question ranks one part above the rest and misses
   them. Search each part in its own words. A second search costs nothing and
   is expected, not a sign you did the first one badly.

4. Before you answer, check that you have the passage that STATES the rule, not
   merely one that applies it or mentions the words. If a passage says "if doing
   so is readily achievable" while explaining parking spaces, it is using a rule
   defined somewhere else; search for that rule, or read_page around it, and
   cite where it is defined. Citing a page that merely contains the phrase sends
   the reader to the wrong place, which is the worst thing an answer can do.

5. Give the specific figures, deadlines and terms the documents use. Do not
   round, summarise away a number, or replace the document's own wording with a
   paraphrase when the exact phrase is what the reader needs.
"""

# "(osha_small_business_handbook.pdf, page 15)" as the model is asked to write
# it. Tolerant of "p 15" and "pg. 15" because models drift on that and the page
# number is what matters.
_CITE_RE = re.compile(
    r"\(\s*([\w\-. ]+?\.(?:pdf|docx|txt|md))\s*,\s*(?:page|p\.?|pg\.?)\s*(\d+)\s*\)",
    re.I,
)


SELECT_PROMPT = """You are building the evidence for an answer. You are not
writing the answer.

Read the question and work out what separate things it asks for. Call each one
an information need. "What should I track for taxes and for travel expenses" is
two needs; "how long do I keep tax records" is one.

Then go through the passages below and, for each need, pick the passages that
actually answer it. For every one you pick, give:

  need   which need it answers, by number
  file   the file name exactly as shown
  page   the page number
  claim  what this passage says about that need, in one sentence
  span   one to three CONSECUTIVE sentences copied EXACTLY from the passage,
         word for word, that support the claim

The span is copied, never paraphrased or shortened with dots. It is checked
against the passage and thrown away if it does not appear there verbatim.

Pick a passage only if it answers a need. Being about the same broad subject is
not enough: a passage saying "this publication explains business taxes" is not
evidence about how long to keep records. If nothing answers a need, leave that
need out; if nothing answers any need, return an empty claims list. An empty
list is a correct and useful answer.

Reply with JSON only, in exactly this shape:

{"needs": ["...", "..."],
 "claims": [{"need": 1, "file": "x.pdf", "page": 12,
             "claim": "...", "span": "..."}]}
"""


ANSWER_PROMPT = """You are writing the final answer from evidence that has already
been gathered. You cannot search. Everything you may use is below, ordered with
the strongest evidence first.

Work in two steps.

STEP 1. List the passages that actually support an answer to the question, by
file name and page, in the form:

    USING: (file.pdf, page 12), (other.pdf, page 3)

Include only passages that genuinely answer what was asked. Being about the
same broad subject is not enough. If none of them answer it, write
USING: NONE and then say plainly that the documents do not cover it, naming
what they do cover.

STEP 2. Write the answer using only those passages, citing each one as
(file.pdf, page 12) immediately after the claim it supports. Use the full file
name with its extension every time. Give the specific figures, deadlines and
terms the documents use rather than paraphrasing them away. If the question has
several parts, answer every part the evidence supports and say which parts it
does not.

The ordering above is meaningful: earlier passages matched the searches better,
and a passage marked as matching several searches is stronger still. Where two
passages say similar things, prefer the higher one unless the lower one is more
specific to the question.
"""




def _normalise(text: str) -> str:
    """Comparison form for checking a span really came from a passage.

    Models reproduce text with the punctuation they prefer, so a span copied
    faithfully still fails a naive substring test when the PDF used a
    non-breaking hyphen and the model wrote an ordinary one. The benchmark
    scorer learned this the same way, marking a correct answer wrong for
    writing self-inspection with U+2011.
    """
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_LOOKALIKES)
    # Hyphens are deleted, not normalised, because PDFs break words across
    # lines with them. The ADA guide is set in narrow columns and stores
    # "technical assis-\ntance"; the model reads that as "assistance", which is
    # the correct reading and does not appear in the stored text. 166 of this
    # corpus's 316 segments contain at least one such break.
    #
    # Deleting rather than joining also handles the opposite case: a real
    # compound like "self-inspection" broken at its own hyphen. Both forms
    # collapse to the same comparison string, and since this form is only ever
    # used to check that a span came from a passage, nothing displayed changes.
    t = re.sub(r"[-\u2010-\u2015]\s*", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


_LOOKALIKES = {ord(c): r for c, r in {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200a": " ",
}.items()}


def _key_of(tools: DocumentTools, item: Dict[str, Any]) -> Any:
    """The evidence key for an item, now that identity is the segment."""
    for k, v in tools.evidence.items():
        if v is item:
            return k
    return None


def _no_evidence_message(tools: DocumentTools) -> str:
    """Said when nothing survived selection, naming what was there instead."""
    files = sorted({f for f, _ in tools.evidence})
    covered = ("Your documents cover " + ", ".join(files) + ".") if files else ""
    return (
        "I couldn't find anything in your documents that answers that. "
        + covered
        + " Try asking about a more specific part of it."
    ).strip()


def _json_object(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in a reply, however the model wrapped it."""
    if not text:
        return None
    body = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.M)
    start = body.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(body)):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(body[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = body.find("{", start + 1)
    return None


class ToolAgent:
    def __init__(self, *, store: Any, max_turns: int = MAX_TURNS):
        self._store = store
        self._max_turns = max_turns

    async def run(
        self,
        *,
        user_id: int,
        message: str,
        formatted_history: str = "",
        workspace_id: Optional[int] = None,
        file_id: Optional[int] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        accessible_ids = None
        if workspace_id is None:
            accessible_ids = await self._store.workspace_repo.accessible_workspace_ids(user_id)

        tools = DocumentTools(
            store=self._store,
            user_id=user_id,
            workspace_id=workspace_id,
            accessible_workspace_ids=accessible_ids,
            file_id=file_id,
        )

        # Hand over the contents pages rather than waiting to be asked for
        # them. See DocumentTools.workspace_map.
        system = SYSTEM_PROMPT
        try:
            workspace_map = await tools.workspace_map()
            if workspace_map:
                system = (
                    f"{SYSTEM_PROMPT}\n\n"
                    "WHAT IS IN THIS WORKSPACE\n\n"
                    "These are the documents and their contents pages. Use them to "
                    "decide which document and which section should hold the answer, "
                    "then search_within that document or read_page that page. A page "
                    "listed under a heading that names the rule is the page to cite, "
                    "not a page that happens to repeat the words.\n\n"
                    f"{workspace_map}"
                )
        except Exception as e:
            logger.warning({"event": "tool_agent.no_workspace_map", "error": str(e)})

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        if formatted_history:
            messages.append({
                "role": "user",
                "content": f"Earlier in this conversation:\n{formatted_history}",
            })
        messages.append({"role": "user", "content": message})

        searches = 0
        reads = 0
        outlines = 0
        for turn in range(self._max_turns):
            reply = await chat_with_tools(messages, tools=TOOL_SCHEMAS)
            if not reply:
                logger.error({"event": "tool_agent.no_reply", "turn": turn})
                break

            calls = reply.get("tool_calls") or []
            if not calls:
                # The search phase is over. The prose this turn produced is
                # discarded: it was written while the evidence was scattered
                # across four tool messages in four different orderings, which
                # is the condition that had it citing p12, p13 and p14 while
                # the page it needed, p11, sat unmentioned in the first search.
                #
                # The answer is written again, by a separate call that sees one
                # ranked evidence set and nothing else. No tool history, no
                # search transcript, no chance to keep searching.
                if tools.evidence:
                    selection = await self._select_evidence(message, tools)
                    if selection and selection.get("status") == "unparsable":
                        if SELECTOR_STRICT:
                            # For an experiment, a silent fallback is worse than
                            # a failure. It made a benchmark run measure
                            # 0.83 x selector + 0.17 x baseline and report one
                            # number, and no conclusion about either system
                            # could survive that. In production the fallback is
                            # the right behaviour; while measuring, it hides the
                            # thing being measured.
                            logger.error({"event": "tool_agent.selector_failed_strict"})
                            return self._finish(
                                "__SELECTOR_FAILED__ the evidence classifier "
                                "produced no usable output.",
                                tools, searches, reads, outlines, turn + 1,
                                selection=selection,
                            )
                        # Fall back to ranked evidence rather than refusing: a
                        # classifier that failed to speak has said nothing about
                        # whether the documents answer the question.
                        answer = await self._answer_from_evidence(message, tools)
                        if answer:
                            return self._finish(answer, tools, searches, reads, outlines,
                                                turn + 1, selection=selection)
                    if selection is not None and selection.get("status") == "ok" \
                            and not selection["claims"]:
                        # Nothing survived. That is a fact about the evidence,
                        # not a judgement the model had to volunteer, which is
                        # the whole reason refusals regressed when answering was
                        # split off from searching: a ranked list always looks
                        # like evidence.
                        logger.info({
                            "event": "tool_agent.no_evidence",
                            "needs": selection["needs_total"],
                        })
                        return self._finish(
                            _no_evidence_message(tools), tools,
                            searches, reads, outlines, turn + 1,
                            selection=selection,
                        )
                    answer = await self._answer_from_evidence(message, tools, selection)
                    if answer:
                        return self._finish(answer, tools, searches, reads, outlines,
                                            turn + 1, selection=selection)
                answer = (reply.get("content") or "").strip()
                if answer:
                    return self._finish(answer, tools, searches, reads, outlines, turn + 1)
                # No content and no calls is a wasted turn. Ask once for an
                # answer rather than returning silence.
                messages.append(reply)
                messages.append({
                    "role": "user",
                    "content": "Answer the question now, using what the tools returned.",
                })
                continue

            messages.append(reply)
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                # search_within counts as a search. It was added later and the
                # counter did not know about it, so a run that searched three
                # times reported zero and looked like the workspace map had
                # replaced retrieval rather than directed it.
                if name in ("search_documents", "search_within"):
                    searches += 1
                elif name == "read_page":
                    reads += 1
                elif name == "outline":
                    outlines += 1
                output = await tools.run(name, fn.get("arguments") or "{}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": output,
                })

        # Out of turns. Whatever the model last said is better than nothing, but
        # it did not come from a turn that chose to stop, so it is not treated
        # as a finished answer.
        logger.warning({"event": "tool_agent.turns_exhausted", "turns": self._max_turns})
        return {
            "response": (
                "I could not finish looking that up. Please ask about a more specific "
                "part of it."
            ),
            "context_chunks": list(tools.seen.values()),
            "mode": "tools",
            "turns": self._max_turns,
            "searches": searches,
            "reads": reads,
            "outlines": outlines,
            "trace": tools.trace,
            "diagnosis": {"stopped": "ran out of turns"},
        }


    async def _select_evidence(
        self, question: str, tools: DocumentTools
    ) -> Optional[Dict[str, Any]]:
        """Turn the evidence set into verified claims, or None if that fails.

        None means "carry on as before". A selector that cannot produce usable
        output must not be able to make the product worse than not having one,
        so every failure path here falls back to answering from ranked evidence.
        """
        groups = tools.clustered_evidence(limit=EVIDENCE_LIMIT)
        if not groups:
            return None
        for g in groups:
            for e in g["pages"]:
                tools.advance((e["file_name"], e["page_number"]), "clustered")

        blocks = []
        for g in groups:
            for e in g["pages"]:
                blocks.append(
                    f"--- {e['file_name']}, page {e['page_number']} ---\n{e['content']}"
                )
        prompt = (
            f"{SELECT_PROMPT}\n\nQUESTION: {question}\n\nPASSAGES:\n\n"
            + "\n\n".join(blocks)
        )
        parsed = None
        attempts_used = 0
        for attempt in range(SELECTOR_ATTEMPTS):
            attempts_used = attempt + 1
            raw = await generate_explanation(prompt, max_context_tokens=MAX_TOKENS_CONTEXT)
            parsed = _json_object(raw)
            if parsed and isinstance(parsed.get("needs"), list):
                break
            # Measured at 17% of questions on the benchmark corpus, which is
            # enough to make a run evaluate two different systems at once: the
            # ones the classifier handled and the ones that bypassed it. The
            # same shape as the empty-content retry in llm_service, and the
            # same fix, because the failure is a model that did not answer
            # rather than a model that answered wrongly.
            logger.info({"event": "tool_agent.selector_retry", "attempt": attempt + 1})
            prompt = (
                "Your previous reply could not be read. Reply with a single JSON "
                "object and nothing else: no explanation, no code fence, no text "
                "before or after it.\n\n" + prompt
            )
        if not parsed or not isinstance(parsed.get("needs"), list):
            # Counted, not silently absorbed. This path used to return None and
            # fall back to answering from ranked evidence, so a benchmark run
            # was silently evaluating two different systems at once: some
            # questions went through the classifier and some bypassed it, and
            # the aggregate could not tell you which. It also inflated
            # "answers no information need", because a question the classifier
            # never ran on looked identical to one where it rejected everything.
            logger.warning({"event": "tool_agent.selector_unparsable"})
            return {"status": "unparsable", "needs": [], "claims": [],
                    "needs_total": 0, "needs_covered": 0, "sufficient": False,
                    "attempts": attempts_used}

        needs = [str(n) for n in parsed.get("needs") or []]
        verified: List[Dict[str, Any]] = []
        for c in parsed.get("claims") or []:
            try:
                key = (str(c["file"]).strip(), int(c["page"]))
            except Exception:
                continue
            # A page can hold more than one segment, so the span is checked
            # against every segment on it. Checking only one is how IRS 334
            # page 14 failed: its cash-versus-accrual text lives in a different
            # segment from its tax-year text.
            candidates = tools.segments_on_page(key[0], key[1])
            if not candidates:
                continue
            span = str(c.get("span") or "")
            if not span.strip():
                continue
            wanted = _normalise(span)
            hit = next(
                (e for e in candidates if wanted and wanted in _normalise(e["content"])),
                None,
            )
            if hit is None:
                for e in candidates:
                    tools.reject(_key_of(tools, e), "span not found in the passage")
                continue
            hit_key = _key_of(tools, hit)
            tools.advance(hit_key, "verified")
            tools.advance(hit_key, "selected")
            verified.append({
                "need": c.get("need"), "claim": str(c.get("claim") or "").strip(),
                "span": span.strip(), "file_name": key[0], "page_number": key[1],
                "file_url": hit.get("file_url"),
            })

        for key, e in tools.evidence.items():
            if (e.get("stage") or "retrieved") in ("retrieved", "clustered") \
                    and not e.get("rejected_because"):
                tools.reject(key, "answers no information need")

        # Coverage is counted, not asked for. A need with no verified claim is
        # a gap whether or not the model would have admitted to one.
        covered = {c["need"] for c in verified if c.get("need") is not None}
        return {
            "status": "ok",
            # How many tries it took to get structured output. If most failures
            # disappear on the second attempt the retry is enough; if they
            # persist, the fix is a stronger constraint than asking again, and
            # this is the number that says which.
            "attempts": attempts_used,
            "needs": needs,
            "claims": verified,
            "needs_total": len(needs),
            "needs_covered": len(covered),
            "sufficient": bool(needs) and len(covered) >= len(needs),
        }

    async def _answer_from_evidence(
        self, question: str, tools: DocumentTools,
        selection: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Write the answer from the ranked evidence set alone.

        One model, one job. The search loop decided what to look for; this
        decides what the answer is. Splitting them is what restores the thing
        the fixed pipeline had and the agent had lost: a single ordered list of
        evidence in front of the model at the moment it writes.
        """
        if selection and selection.get("claims"):
            lines = []
            for c in selection["claims"]:
                lines.append(
                    f"--- {c['file_name']}, page {c['page_number']} ---\n"
                    f"[cite this passage as: ({c['file_name']}, page {c['page_number']})]\n"
                    f"{c['span']}"
                )
            gaps = ""
            if not selection.get("sufficient"):
                gaps = ("\n\nThe evidence does not cover every part of the question. "
                        "Answer the parts it covers and say plainly which parts the "
                        "documents do not address.")
            body = ("Verified evidence. Each span below was checked against the "
                    "document it came from.\n\n" + "\n\n".join(lines) + gaps)
        else:
            body = tools.render_evidence(header="Evidence gathered", limit=EVIDENCE_LIMIT)

        prompt = f"{ANSWER_PROMPT}\n\nQUESTION: {question}\n\n{body}"
        out = await generate_explanation(prompt, max_context_tokens=MAX_TOKENS_CONTEXT)
        if not out:
            return ""
        # The USING: line is scaffolding that made the model commit to its
        # sources before writing prose. The customer does not need to read it.
        cleaned = re.sub(r"(?im)^\s*(STEP\s*[12][.:]?|USING:).*$", "", out).strip()
        return cleaned or out.strip()

    @staticmethod
    def _diagnose(tools: DocumentTools, used: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The questions a final answer cannot answer on its own.

        Was retrieval good and the reasoning poor, or was retrieval bad? Did it
        stop too early? Did it ask the same thing twice? Each of these is a
        different fix, and all four look identical from the outside.
        """
        searches = [t for t in tools.trace if t["tool"].startswith("search")]
        wasted = [t for t in searches if t["returned"] and not t["new_pages"]]
        cited = {(c["file_name"], c["page_number"]) for c in used}
        return {
            # How far each passage got between being found and being cited,
            # with a reason on every drop.
            "lifecycle": tools.lifecycle(),
            # Retrieval showed it these pages and the answer used none of them.
            # High means retrieval was fine and the answer ignored it.
            "pages_seen": len(tools.seen),
            "pages_cited": len(cited),
            # A search that returned only pages already seen cost a round trip
            # and taught it nothing.
            "repeat_searches": len(wasted),
            "queries": [t["args"].get("query") for t in searches if t["args"].get("query")],
            "documents_touched": len({f for f, _ in tools.seen}),
            "documents_cited": len({f for f, _ in cited}),
        }

    def _finish(
        self, answer: str, tools: DocumentTools, searches: int, reads: int,
        outlines: int, turns: int, selection: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        answer, used = self._verify_citations(answer, tools)
        source_map = "\n".join(
            ["\n\n**Sources:**"]
            + [
                f"{n}. [{c['file_name']} (Page {c['page_number']})]"
                f"({c.get('file_url') or '#'}#page={c['page_number']})"
                for n, c in enumerate(used, start=1)
            ]
        )
        # searches and reads are logged because the score alone cannot say
        # whether a change to the instructions changed the behaviour. An
        # instruction telling the model to search twice is worth nothing if it
        # still searches once, and the number is the only way to tell.
        for c in used:
            tools.advance((c["file_name"], c["page_number"]), "cited")
        # Everything the answer step was shown and did not use. This is the
        # 63% bucket: pages retrieval delivered and the answer walked past.
        for key, e in tools.evidence.items():
            if (e.get("stage") or "retrieved") != "cited" and not e.get("rejected_because"):
                tools.reject(key, "shown to the answer step, not cited")
        diagnosis = self._diagnose(tools, used)
        logger.info({
            "event": "tool_agent.answered",
            "turns": turns, "searches": searches, "reads": reads,
            "outlines": outlines, "citations": len(used), **diagnosis,
        })
        return {
            "response": answer + ("\n\n" + source_map if used else ""),
            "context_chunks": list(tools.seen.values()),
            "mode": "tools",
            "turns": turns,
            "searches": searches,
            "reads": reads,
            "outlines": outlines,
            "trace": tools.trace,
            "diagnosis": diagnosis,
            "selection": {
                k: v for k, v in (selection or {}).items() if k != "claims"
            } or None,
        }

    def _verify_citations(
        self, answer: str, tools: DocumentTools
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Turn the model's claimed pages into links, keeping only real ones.

        A citation to a page no tool ever returned is removed from the text
        rather than rendered. The customer's whole reason to trust an answer is
        that following the link lands on the passage, so a link that cannot do
        that is worse than no link.
        """
        used: List[Dict[str, Any]] = []
        numbering: Dict[Tuple[str, int], int] = {}
        dropped: List[str] = []

        def replace(match: re.Match) -> str:
            name, page = match.group(1).strip(), int(match.group(2))
            seen = tools.seen.get((name, page))
            if seen is None:
                # Try a case-insensitive match before giving up: the model
                # echoing a file name with different capitalisation is a
                # transcription slip, not an invented source.
                seen = next(
                    (
                        v for (n, p), v in tools.seen.items()
                        if p == page and n.lower() == name.lower()
                    ),
                    None,
                )
            if seen is None:
                dropped.append(f"{name} p{page}")
                return ""
            key = (seen["file_name"], page)
            if key not in numbering:
                used.append(seen)
                numbering[key] = len(used)
            n = numbering[key]
            return f"[[{n}]]({seen.get('file_url') or '#'}#page={page})"

        cleaned = _CITE_RE.sub(replace, answer)
        # Counted, not silent. Answers were arriving with no citations at all
        # because the model wrote "Publication 583, page 12", which names no
        # file; every one was dropped and nothing said so. If this number is
        # ever large, the instruction is losing, not the retrieval.
        if dropped:
            logger.warning({
                "event": "tool_agent.dropped_citations",
                "count": len(dropped), "kept": len(used), "dropped": dropped[:5],
            })
        return cleaned, used
