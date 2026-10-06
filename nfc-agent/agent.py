"""nfc-agent: read tag UIDs from a USB NFC reader and stream them over HTTP.

The Arduino App container cannot open USB devices (its device cgroup has no
rule for major 189), so reading runs in this separate container instead.

Two backends run side by side, so either kind of reader can be plugged in:
  nfcpy  Sony RC-S380 and other readers nfcpy has a driver for
  pcsc   CCID (PC/SC) readers such as the Zoweetek ZW-12026-12, via pcscd (see pcsc.py)
NFC_BACKENDS=nfcpy or NFC_BACKENDS=pcsc runs only one.

  GET /events  -> NDJSON stream, one line per touch: {"uid": "04A1B2C3D4E5F6", "ts": 1727330000.1}
  GET /health  -> {"reader": true|false, "path": "usb:054c:06c3", "backends": {"nfcpy": ..., "pcsc": ...}}
"""
import json
import logging
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("AGENT_PORT", "8100"))
DEVICE = os.environ.get("NFC_DEVICE", "usb")
BACKENDS = [b.strip() for b in os.environ.get("NFC_BACKENDS", "nfcpy,pcsc").split(",") if b.strip()]
DEBOUNCE_S = float(os.environ.get("DEBOUNCE_S", "1.5"))
RETRY_S = 3.0

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nfc-agent")

# backends: what each backend has open right now (None = no reader); reader/path summarise them.
state = {"reader": False, "path": None, "backends": {}}
state_lock = threading.Lock()
subscribers: set[queue.Queue] = set()
sub_lock = threading.Lock()


def set_reader(backend: str, path: str | None) -> None:
    with state_lock:
        state["backends"][backend] = path
        paths = [p for p in state["backends"].values() if p]
        state.update(reader=bool(paths), path=paths[0] if paths else None)


def publish(uid: str) -> None:
    event = {"uid": uid, "ts": time.time()}
    log.info("touch %s", uid)
    with sub_lock:
        for q in subscribers:
            q.put(event)


class Debouncer:
    """The reader reports the tag repeatedly while it is held; pass on only the first read."""

    def __init__(self, window_s: float = DEBOUNCE_S, clock=time.monotonic):
        self.window_s, self.clock = window_s, clock
        self.last_uid, self.last_ts = None, 0.0
        self.lock = threading.Lock()

    def __call__(self, uid: str) -> bool:
        with self.lock:
            now = self.clock()
            fresh = uid != self.last_uid or now - self.last_ts > self.window_s
            self.last_uid, self.last_ts = uid, now
            return fresh


debounce = Debouncer()


def on_uid(uid: str) -> None:
    if debounce(uid):
        publish(uid)


def nfcpy_loop() -> None:
    import nfc  # imported here so fake_agent and a PC/SC-only setup do not need nfcpy

    # nfcpy logs an ERROR on every failed open; set this after the import, which resets the level.
    logging.getLogger("nfc").setLevel(logging.CRITICAL)

    warned = False

    def on_connect(tag) -> bool:
        on_uid(tag.identifier.hex().upper())
        return False  # release immediately; we only need the UID

    while True:
        try:
            with nfc.ContactlessFrontend(DEVICE) as clf:
                set_reader("nfcpy", str(clf.device))
                log.info("reader opened: %s", clf.device)
                while clf.connect(rdwr={"on-connect": on_connect, "beep-on-connect": False}):
                    pass
        except (IOError, OSError) as e:
            if state["backends"].get("nfcpy"):
                log.warning("reader lost: %s", e)
            elif not warned:
                log.info("no nfcpy reader on %r, retrying every %.0fs", DEVICE, RETRY_S)
            warned = True
        set_reader("nfcpy", None)
        time.sleep(RETRY_S)


def pcsc_loop() -> None:
    import pcsc

    pcsc.run(on_uid, lambda readers: set_reader("pcsc", ", ".join(readers) or None))


LOOPS = {"nfcpy": nfcpy_loop, "pcsc": pcsc_loop}


def keep_running(name: str) -> None:
    """Restart a backend that crashed, so one reader type cannot take the agent down."""
    while True:
        try:
            LOOPS[name]()
        except Exception:
            log.exception("%s backend crashed; restarting in %.0fs", name, RETRY_S)
        set_reader(name, None)
        time.sleep(RETRY_S)


def start_backends() -> None:
    for name in BACKENDS:
        if name not in LOOPS:
            raise SystemExit(f"unknown NFC_BACKENDS entry {name!r} (use {', '.join(LOOPS)})")
        set_reader(name, None)
        threading.Thread(target=keep_running, args=(name,), daemon=True, name=name).start()
    log.info("backends: %s", ", ".join(BACKENDS))


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
            with state_lock:
                body = json.loads(json.dumps(state))
            return self._json(200, body)
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
    start_backends()
    log.info("listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
