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
                    answer = await self._answer_from_evidence(message, tools)
                    if answer:
                        return self._finish(answer, tools, searches, reads, outlines, turn + 1)
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

    async def _answer_from_evidence(self, question: str, tools: DocumentTools) -> str:
        """Write the answer from the ranked evidence set alone.

        One model, one job. The search loop decided what to look for; this
        decides what the answer is. Splitting them is what restores the thing
        the fixed pipeline had and the agent had lost: a single ordered list of
        evidence in front of the model at the moment it writes.
        """
        prompt = (
            f"{ANSWER_PROMPT}\n\n"
            f"QUESTION: {question}\n\n"
            f"{tools.render_evidence(header='Evidence gathered', limit=EVIDENCE_LIMIT)}"
        )
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
        outlines: int, turns: int
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
