"""Load settings from data/app.env into os.environ.

arduino-app-cli offers no way to pass environment variables to the App
container, so MGMT_URL, DEVICE_TOKEN, ADMIN_PASSWORD, ... are read from this
file instead (data/ is git-ignored). Real environment variables win.
"""
import os
from pathlib import Path


def load_env_file(path: Path | None = None) -> Path | None:
    if path is None:
        base = Path("/app") if Path("/app/app.yaml").exists() else Path.cwd()
        path = base / "data" / "app.env"
    if not path.exists():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return path
