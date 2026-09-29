import asyncio
import threading
from datetime import datetime, timezone


class EventHub:
    """Fan-out of events to WebSocket clients.

    publish() is called from sync request handlers (thread pool), so events are
    handed to the event loop with call_soon_threadsafe.
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def publish(self, type_: str, **data) -> None:
        event = {"type": type_, "at": datetime.now(timezone.utc).isoformat(), **data}
        if self._loop is None:
            return
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            self._loop.call_soon_threadsafe(_put_nowait, q, event)


def _put_nowait(q: asyncio.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        pass  # slow client; it will refresh on its next full reload
