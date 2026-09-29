"""NFC equipment lending terminal.

Phase 1 runs both layers in this App: Management (FastAPI on :8000, in a thread)
and Edge (reads UIDs from nfc-agent, drives the LED via the sketch).
Phase 2 sets RUN_MANAGEMENT=false, MGMT_URL and DEVICE_TOKEN in data/app.env.
"""
import logging
import os
import queue
import threading
import time
from pathlib import Path

from edge.envfile import load_env_file

env_file = load_env_file()  # must run before Settings() reads the environment

from arduino.app_utils import App  # noqa: E402

from edge.display import BridgeDisplay  # noqa: E402
from edge.mgmt_client import MgmtClient  # noqa: E402
from edge.nfc_reader import AgentReader, poll_forever  # noqa: E402
from edge.touch_fsm import Terminal  # noqa: E402
from management.app import serve  # noqa: E402
from management.config import Settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")
if env_file:
    log.info("loaded settings from %s", env_file)

settings = Settings()
RUN_MANAGEMENT = os.environ.get("RUN_MANAGEMENT", "true").lower() != "false"
MGMT_URL = os.environ.get("MGMT_URL", f"http://127.0.0.1:{settings.port}")
AGENT_URL = os.environ.get("AGENT_URL", f"http://{os.environ.get('HOST_IP', '127.0.0.1')}:8100")


def device_token() -> str:
    token = os.environ.get("DEVICE_TOKEN")
    if token:
        return token
    path: Path = settings.data_dir / "device_token"
    for _ in range(100):  # Management writes it on first start
        if path.exists():
            return path.read_text().strip()
        time.sleep(0.1)
    raise RuntimeError(f"no device token: set DEVICE_TOKEN in data/app.env or create {path}")


if RUN_MANAGEMENT:
    threading.Thread(target=serve, args=(settings, True), daemon=True, name="management").start()
    log.info("management: http://<this board>:%d/", settings.port)

client = MgmtClient(MGMT_URL, settings.local_device_id, device_token())
terminal = Terminal(client, BridgeDisplay())
uids: queue.Queue[str] = queue.Queue()
reader = AgentReader(AGENT_URL, uids.put)
reader.start()
threading.Thread(target=poll_forever, args=(reader, client.heartbeat), daemon=True, name="heartbeat").start()
terminal.start()
log.info("edge: device=%s mgmt=%s agent=%s", settings.local_device_id, MGMT_URL, AGENT_URL)


def loop():
    try:
        terminal.handle_uid(uids.get(timeout=0.2), time.monotonic())
    except queue.Empty:
        pass
    terminal.tick(time.monotonic())


App.run(user_loop=loop)
