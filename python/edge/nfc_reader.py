"""Reader adapter: receives UIDs from nfc-agent (a separate container that owns the USB reader).

The App container cannot open USB devices, so nfc-agent streams touches as NDJSON
from GET /events. Swapping the reading method only means replacing this module.
"""
import json
import logging
import threading
import time
from typing import Callable

import httpx

log = logging.getLogger("edge.nfc_reader")


class AgentReader:
    def __init__(self, agent_url: str, on_uid: Callable[[str], None], retry_s: float = 3.0):
        self.url = agent_url.rstrip("/")
        self.on_uid = on_uid
        self.retry_s = retry_s
        self.connected = False
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="nfc-reader").start()

    def stop(self) -> None:
        self._stop.set()

    def reader_ok(self) -> bool | None:
        """Whether nfc-agent has a reader attached (None if the agent is unreachable)."""
        try:
            return bool(httpx.get(f"{self.url}/health", timeout=2).json().get("reader"))
        except (httpx.HTTPError, ValueError):
            return None

    def _run(self) -> None:
        warned = False
        while not self._stop.is_set():
            try:
                # The agent sends a blank keep-alive line every 15 s, so 60 s of silence means it is gone.
                with httpx.stream("GET", f"{self.url}/events", timeout=httpx.Timeout(5, read=60)) as r:
                    r.raise_for_status()
                    self.connected, warned = True, False
                    log.info("connected to nfc-agent at %s", self.url)
                    for line in r.iter_lines():
                        if self._stop.is_set():
                            return
                        if line.strip():
                            self._handle(line)
            except httpx.HTTPError as e:
                if self.connected or not warned:
                    log.warning("nfc-agent unavailable (%s); retrying every %.0fs", e, self.retry_s)
                    warned = True
            self.connected = False
            self._stop.wait(self.retry_s)

    def _handle(self, line: str) -> None:
        try:
            uid = json.loads(line)["uid"]
        except (ValueError, KeyError, TypeError):
            log.warning("ignored malformed event: %r", line[:100])
            return
        self.on_uid(uid)


def poll_forever(reader: AgentReader, send_heartbeat: Callable[[bool | None], None], interval_s: float = 60) -> None:
    """Heartbeat loop: report whether the reader is attached so the Web UI can show it."""
    while True:
        try:
            send_heartbeat(reader.reader_ok())
        except Exception as e:  # Management down: the next touch will show OFFLINE anyway
            log.debug("heartbeat failed: %s", e)
        time.sleep(interval_s)
