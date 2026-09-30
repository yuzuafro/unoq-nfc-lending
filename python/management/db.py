from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


# Columns added after the first release. create_all only creates missing tables,
# so existing databases get these via ALTER TABLE on startup.
ADDED_COLUMNS = {
    "items": {"asset_no": "VARCHAR(64) NOT NULL DEFAULT ''"},
    "users": {"team": "VARCHAR(100) NOT NULL DEFAULT ''"},
}


def _add_missing_columns(engine: Engine) -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in ADDED_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def make_session_factory(engine: Engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
