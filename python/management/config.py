import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    # In the App container the app folder is mounted at /app; keep data next to it
    # so it survives restarts. Outside the container fall back to ./data.
    base = Path("/app") if Path("/app/app.yaml").exists() else Path.cwd()
    return base / "data"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", _default_data_dir())))
    database_url: str = os.environ.get("DATABASE_URL", "")
    host: str = os.environ.get("MGMT_HOST", "0.0.0.0")
    port: int = int(os.environ.get("MGMT_PORT", "8000"))
    admin_username: str = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "")
    session_hours: int = int(os.environ.get("ADMIN_SESSION_HOURS", "12"))
    registration_seconds: int = 60
    backup_generations: int = 7
    local_device_id: str = os.environ.get("DEVICE_ID", "unoq-1")

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'nfc.db'}"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")
