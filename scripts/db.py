import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.environ.get("MVP_DB_PATH", os.path.join(BASE_DIR, "data", "mvp.sqlite3"))

# The schema only needs checking once per process, not on every connection.
_schema_ready = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for ddl in [
        """CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
            base_url TEXT, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS statements (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_url TEXT NOT NULL,
            original_id TEXT, original_url TEXT, title TEXT, content_html TEXT,
            content_text TEXT, media_url TEXT, published_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (source_id) REFERENCES sources(id)
        )""",
        """CREATE TABLE IF NOT EXISTS indicators (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, series_id TEXT NOT NULL,
            frequency TEXT NOT NULL, source TEXT NOT NULL, unit TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS indicator_points (
            indicator_id TEXT NOT NULL, date TEXT NOT NULL, value REAL,
            ingested_at TEXT NOT NULL, PRIMARY KEY (indicator_id, date),
            FOREIGN KEY (indicator_id) REFERENCES indicators(id)
        )""",
        """CREATE TABLE IF NOT EXISTS intraday_points (
            series_id TEXT NOT NULL, ts_utc TEXT NOT NULL, value REAL,
            source TEXT NOT NULL, ingested_at TEXT NOT NULL,
            PRIMARY KEY (series_id, ts_utc)
        )""",
        """CREATE TABLE IF NOT EXISTS link_previews (
            url TEXT PRIMARY KEY, title TEXT, description TEXT,
            image_url TEXT, site_name TEXT, status TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )""",
        # Statements are filtered and sorted by publish time on every page load.
        "CREATE INDEX IF NOT EXISTS idx_statements_published_at ON statements (published_at)",
    ]:
        conn.execute(ddl)

    # Migration for databases created before statements.media_url existed.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(statements)")}
    if "media_url" not in columns:
        conn.execute("ALTER TABLE statements ADD COLUMN media_url TEXT")

    conn.commit()


def init_schema() -> None:
    """Create or migrate the schema now, rather than lazily on the first connect()."""
    global _schema_ready
    _schema_ready = False
    with connect():
        pass


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, and always close it."""
    global _schema_ready
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if not _schema_ready:
            _ensure_schema(conn)
            _schema_ready = True
        with conn:
            yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
