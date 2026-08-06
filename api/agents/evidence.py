"""The evidence set: one object, accumulated across however many retrievals.

The graph's state is this. Retrieval adds to it, the coverage check reads it,
the answer is written from it, and citations are verified against it. Nothing
else holds retrieved passages, because two places holding them is how the
pipeline and the agent drifted apart: the agent kept its own accumulation, lost
the global ordering the pipeline gets for free, and scored 14.5 against 17.0
while calling exactly the same search function.

RANKING ACROSS RETRIEVALS

Scores from different queries are not comparable. Each query produces its own
distribution, so a 0.61 from one search says nothing about a 0.58 from another.
Ranks are comparable, so a passage is scored by reciprocal rank summed over
every search that returned it:

    score = sum over searches of  1 / (60 + rank in that search)

Which is the same reciprocal rank fusion the SQL already uses to combine the
vector and keyword halves, applied one level up to combine the retrievals. A
passage two different searches both rank highly is stronger evidence than one a
single search happened to return, and summing is what says so.

IDENTITY IS THE SEGMENT

Not the page. IRS 334 page 14 is two segments, one about tax years and one
about cash versus accrual, and keying on (file, page) let the second silently
overwrite the first, with the winner decided by retrieval order. Citations are
still per page, because a page is what a reader opens.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# The constant from the reciprocal-rank-fusion literature. It stops the top
# result of any single search dominating everything the others found.
RRF_K = 60


class EvidenceSet:
    """Passages found so far, ranked once, across every retrieval."""

    def __init__(self) -> None:
        self._items: Dict[Any, Dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._items)

    def add(self, chunks: Iterable[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Fold one retrieval in. Returns the passages that are new.

        What is new is the signal the coverage check runs on: a search that
        returns only passages already held has told us nothing, and that is
        invisible in a count of searches.
        """
        added: List[Dict[str, Any]] = []
        for rank, c in enumerate(chunks or [], start=1):
            key = c.get("segment_id") or c.get("chunk_id")
            if key is None:
                key = (c.get("file_name"), c.get("page_number"), rank)
            item = self._items.get(key)
            if item is None:
                item = {
                    "segment_id": c.get("segment_id"),
                    "file_name": c.get("file_name"),
                    "page_number": c.get("page_number"),
                    "file_url": c.get("file_url"),
                    "meta_data": c.get("meta_data") or {},
                    "content": c.get("content") or "",
                    "score": 0.0,
                    "hits": 0,
                    "queries": [],
                }
                self._items[key] = item
                added.append(item)
            item["score"] += 1.0 / (RRF_K + rank)
            item["hits"] += 1
            if query and query not in item["queries"]:
                item["queries"].append(query)
        return added

    def ranked(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Everything, best first. The one ordering the answer step sees."""
        out = sorted(self._items.values(), key=lambda e: e["score"], reverse=True)
        return out[:limit] if limit else out

    def pages(self) -> set:
        return {(e["file_name"], e["page_number"]) for e in self._items.values()}

    def as_chunks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Shaped the way the answer step already expects chunks to look.

        Keeps the existing citation mechanism intact: the answer cites
        [Segment N] against this ordering and the code resolves N to a file and
        a page. The model emits one number rather than transcribing a
        forty-character filename, which is a failure mode it cannot have.
        """
        return [
            {
                "chunk_id": None,
                "segment_id": e["segment_id"],
                "content": e["content"],
                "file_name": e["file_name"],
                "file_url": e["file_url"],
                "page_number": e["page_number"],
                "meta_data": e["meta_data"],
                "hybrid_score": e["score"],
                "similarity_score": e["score"],
            }
            for e in self.ranked(limit)
        ]
