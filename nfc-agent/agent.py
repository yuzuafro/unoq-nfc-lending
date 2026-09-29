"""nfc-agent: read tag UIDs from a Sony RC-S380 and stream them over HTTP.

The Arduino App container cannot open USB devices (its device cgroup has no
rule for major 189), so reading runs in this separate container instead.

  GET /events  -> NDJSON stream, one line per touch: {"uid": "04A1B2C3D4E5F6", "ts": 1727330000.1}
  GET /health  -> {"reader": true|false, "path": "usb:054c:06c3"}
"""
import json
import logging
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import nfc

PORT = int(os.environ.get("AGENT_PORT", "8100"))
DEVICE = os.environ.get("NFC_DEVICE", "usb")
DEBOUNCE_S = float(os.environ.get("DEBOUNCE_S", "1.5"))
RETRY_S = 3.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nfc-agent")
logging.getLogger("nfc").setLevel(logging.CRITICAL)  # nfcpy logs an ERROR on every failed open

state = {"reader": False, "path": None}
subscribers: set[queue.Queue] = set()
sub_lock = threading.Lock()


def publish(uid: str) -> None:
    event = {"uid": uid, "ts": time.time()}
    log.info("touch %s", uid)
    with sub_lock:
        for q in subscribers:
            q.put(event)


def reader_loop() -> None:
    last_uid, last_ts = None, 0.0
    warned = False

    def on_connect(tag) -> bool:
        nonlocal last_uid, last_ts
        uid = tag.identifier.hex().upper()
        now = time.monotonic()
        # The reader reports the tag repeatedly while it is held; ignore repeats.
        if uid != last_uid or now - last_ts > DEBOUNCE_S:
            publish(uid)
        last_uid, last_ts = uid, now
        return False  # release immediately; we only need the UID

    while True:
        try:
            with nfc.ContactlessFrontend(DEVICE) as clf:
                state.update(reader=True, path=str(clf.device))
                log.info("reader opened: %s", clf.device)
                while clf.connect(rdwr={"on-connect": on_connect, "beep-on-connect": False}):
                    pass
        except (IOError, OSError) as e:
            if state["reader"]:
                log.warning("reader lost: %s", e)
            elif not warned:
                log.warning("reader not found on %r, retrying every %.0fs", DEVICE, RETRY_S)
            warned = True
        state.update(reader=False, path=None)
        time.sleep(RETRY_S)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, state)
        if self.path != "/events":
            return self._json(404, {"error": "not found"})
        q: queue.Queue = queue.Queue()
        with sub_lock:
            subscribers.add(q)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    line = json.dumps(q.get(timeout=15)) + "\n"
                except queue.Empty:
                    line = "\n"  # keep-alive so dead clients are detected
                self.wfile.write(line.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with sub_lock:
                subscribers.discard(q)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=reader_loop, daemon=True).start()
    log.info("listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
