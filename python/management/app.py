import asyncio
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import Settings
from .db import make_engine, make_session_factory
from .events import EventHub
from .security import SessionStore
from .service import Service

log = logging.getLogger("management")
STATIC = Path(__file__).parent / "static"


class Backups:
    """Daily SQLite snapshots in <data>/backups, keeping the newest N."""

    def __init__(self, db_path: Path, backup_dir: Path, keep: int):
        self.db_path, self.dir, self.keep = db_path, backup_dir, keep

    def snapshot(self, name: str | None = None) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        dest = self.dir / (name or f"nfc-{datetime.now():%Y%m%d-%H%M%S}.db")
        src, dst = sqlite3.connect(self.db_path), sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        return dest

    def daily(self) -> None:
        name = f"nfc-daily-{datetime.now():%Y%m%d}.db"
        if not (self.dir / name).exists():
            self.snapshot(name)
            log.info("backup written: %s", name)
        for old in sorted(self.dir.glob("nfc-daily-*.db"))[:-self.keep]:
            old.unlink()
        for manual in sorted(self.dir.glob("nfc-2*.db"))[:-self.keep]:
            manual.unlink()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.daily()
            except Exception:
                log.exception("backup failed")
            stop.wait(3600)


def _write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    os.chmod(path, 0o600)


def bootstrap(settings: Settings, service: Service, local_device: bool) -> None:
    """First-run setup: an admin account and (Phase 1) the local terminal's token."""
    password = settings.admin_password
    pw_file = settings.data_dir / "admin_initial_password.txt"
    if not password:
        password = secrets.token_urlsafe(9)
    if service.ensure_admin(settings.admin_username, password):
        if not settings.admin_password:
            _write_secret(pw_file, password)
            log.warning("created admin '%s'; initial password saved in %s (change it in 設定)",
                        settings.admin_username, pw_file)
        else:
            log.info("created admin '%s' from ADMIN_PASSWORD", settings.admin_username)

    if local_device:
        token_file = settings.data_dir / "device_token"
        known = {d["id"] for d in service.list_devices()}
        if settings.local_device_id not in known or not token_file.exists():
            token = service.create_device(settings.local_device_id, "UNO Q（ローカル）")
            _write_secret(token_file, token)
            log.info("issued token for local device '%s'", settings.local_device_id)


def create_app(settings: Settings | None = None, local_device: bool = False) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    hub = EventHub()
    service = Service(make_session_factory(engine), hub, settings.registration_seconds)
    bootstrap(settings, service, local_device)

    backups = None
    if settings.is_sqlite:
        db_path = Path(engine.url.database)
        backups = Backups(db_path, settings.data_dir / "backups", settings.backup_generations)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        if backups:
            threading.Thread(target=backups.run_forever, args=(stop,), daemon=True, name="backup").start()
        yield
        stop.set()

    app = FastAPI(title="NFC備品管理 Management API", lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service
    app.state.hub = hub
    app.state.sessions = SessionStore(settings.session_hours * 3600)
    app.state.backups = backups

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": {"code": "validation",
                            "message": "入力内容を確認してください", "errors": exc.errors()}},)

    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def serve(settings: Settings | None = None, local_device: bool = False) -> None:
    import uvicorn

    settings = settings or Settings()
    app = create_app(settings, local_device)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
