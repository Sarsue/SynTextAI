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
import re
from typing import Any, Dict, List, Optional, Tuple

from api.agents.document_tools import TOOL_SCHEMAS, DocumentTools
from api.services.llm_service import chat_with_tools

logger = logging.getLogger(__name__)

# Enough turns to search twice, read a page to check a figure, and answer.
# Unbounded loops are how an agent turns one question into a bill.
MAX_TURNS = 6

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

        messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if formatted_history:
            messages.append({
                "role": "user",
                "content": f"Earlier in this conversation:\n{formatted_history}",
            })
        messages.append({"role": "user", "content": message})

        searches = 0
        reads = 0
        for turn in range(self._max_turns):
            reply = await chat_with_tools(messages, tools=TOOL_SCHEMAS)
            if not reply:
                logger.error({"event": "tool_agent.no_reply", "turn": turn})
                break

            calls = reply.get("tool_calls") or []
            if not calls:
                answer = (reply.get("content") or "").strip()
                if answer:
                    return self._finish(answer, tools, searches, reads, turn + 1)
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
                if name == "search_documents":
                    searches += 1
                elif name == "read_page":
                    reads += 1
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
        }

    def _finish(
        self, answer: str, tools: DocumentTools, searches: int, reads: int, turns: int
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
        logger.info({
            "event": "tool_agent.answered",
            "turns": turns, "searches": searches, "reads": reads,
            "citations": len(used),
        })
        return {
            "response": answer + ("\n\n" + source_map if used else ""),
            "context_chunks": list(tools.seen.values()),
            "mode": "tools",
            "turns": turns,
            "searches": searches,
            "reads": reads,
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
