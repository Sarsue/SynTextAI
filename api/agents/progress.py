"""What the reader sees while an answer is being made.

Before this, a question showed "Reading your documents…" for the whole 10 to
40 seconds and then the answer appeared at once. Now the reader sees each step
as it happens (searching, reading N documents, writing, checking N claims) and
the answer's text as it is written.

WHAT IS SHOWN EARLY IS NOT THE ANSWER

Streamed text has not been checked yet. The verifier needs the whole answer,
so it runs after writing ends, and the final message, checked and with real
page links, replaces what was streamed. Until then the browser shows citations
as plain numbers and makes nothing clickable, and the progress line says the
citations are being checked. Decided with Osas 2026-09-23: a few seconds of
clearly labelled, unclickable, unchecked text is worth the wait it removes.

THREE PIECES

  Progress        where progress goes. The worker's publisher sends it to the
                  browser; NO_PROGRESS drops it (tests, the MCP path, anything
                  with no one watching).
  RefusalGate     holds back the opening of an answer until it cannot be the
                  refusal word, so a question the documents do not answer
                  never flashes "INSUFFICIENT" on screen.
  Deferred        holds the single-document draft, which starts before the
                  coordinator has decided, until it is known to be the answer.
                  On the multi-document path it is discarded unseen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

REFUSAL = "INSUFFICIENT"


class Progress:
    """Receives the steps and the text. The base class does nothing."""

    def stage(self, name: str, **info: Any) -> None:
        pass

    def text(self, piece: str) -> None:
        pass

    def reset(self) -> None:
        pass


NO_PROGRESS = Progress()


class RefusalGate:
    """Passes text through once the opening cannot be the refusal word.

    The composer's prompt tells the model to reply with the single word
    INSUFFICIENT when the documents do not answer, and code turns that into a
    polite message. Streamed raw, the reader would see the word first.
    """

    def __init__(self, sink: Progress):
        self._sink = sink
        self._held = ""
        self._state = "waiting"  # waiting | open | refused

    def text(self, piece: str) -> None:
        if self._state == "open":
            self._sink.text(piece)
            return
        if self._state == "refused":
            return
        self._held += piece
        head = self._held.lstrip().upper().lstrip("*#_ ")
        if not head:
            return
        if REFUSAL.startswith(head[: len(REFUSAL)]) and len(head) < len(REFUSAL):
            return  # could still become the refusal word
        if head.startswith(REFUSAL):
            self._state = "refused"
            return
        self._state = "open"
        self._sink.text(self._held)
        self._held = ""

    def reset(self) -> None:
        if self._state == "open":
            self._sink.reset()
        self._held = ""
        self._state = "waiting"


class Deferred:
    """Holds text until told where it goes, or that it goes nowhere."""

    def __init__(self) -> None:
        self._pieces: List[Any] = []
        self._target: Optional[Progress] = None
        self._discarded = False

    def text(self, piece: str) -> None:
        if self._discarded:
            return
        if self._target is not None:
            self._target.text(piece)
        else:
            self._pieces.append(("text", piece))

    def reset(self) -> None:
        if self._discarded:
            return
        if self._target is not None:
            self._target.reset()
        else:
            self._pieces = []

    def release(self, target: Progress) -> None:
        for kind, piece in self._pieces:
            if kind == "text":
                target.text(piece)
        self._pieces = []
        self._target = target

    def discard(self) -> None:
        self._discarded = True
        self._pieces = []


Publish = Callable[[Dict[str, Any]], Awaitable[Any]]


class ProgressPublisher(Progress):
    """Sends progress to one browser, bundled about ten times a second.

    Text arrives a few characters at a time; one event per piece would be
    hundreds of events per answer. Everything that arrives within a flush
    interval goes out as one event, in order, with adjacent text merged.
    """

    def __init__(self, publish: Publish, history_id: int, interval: float = 0.1):
        self._publish = publish
        self._history_id = history_id
        self._interval = interval
        self._events: List[Dict[str, Any]] = []
        self._started = time.monotonic()
        self._task: Optional[asyncio.Task] = None
        self._closed = False

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._started, 1)

    def _add(self, event: Dict[str, Any]) -> None:
        if self._closed:
            return
        if event["kind"] == "text" and self._events and self._events[-1]["kind"] == "text":
            self._events[-1]["text"] += event["text"]
        else:
            self._events.append(event)

    def stage(self, name: str, **info: Any) -> None:
        self._add({"kind": "stage", "stage": name, "info": info, "elapsed": self._elapsed()})

    def text(self, piece: str) -> None:
        if piece:
            self._add({"kind": "text", "text": piece})

    def reset(self) -> None:
        # Text still waiting to be sent is part of what is being reset.
        self._events = [e for e in self._events if e["kind"] != "text"]
        self._add({"kind": "reset"})

    async def _flush(self) -> None:
        if not self._events:
            return
        events, self._events = self._events, []
        try:
            await self._publish({"history_id": self._history_id, "events": events})
        except Exception as e:
            # Progress is a courtesy; the answer still arrives on its own.
            logger.debug(f"Progress publish failed: {e}")

    async def _run(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._interval)
            await self._flush()

    def start(self) -> "ProgressPublisher":
        self._task = asyncio.create_task(self._run())
        return self

    async def close(self) -> None:
        """Send what is left, then stop. Call before the final answer goes."""
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._flush()
