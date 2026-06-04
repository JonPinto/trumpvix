import os
import sqlite3

from db import DB_PATH


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def main() -> None:
    data_dir = os.path.dirname(DB_PATH)
    ensure_dir(data_dir)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                base_url TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS statements (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                original_id TEXT,
                original_url TEXT,
                title TEXT,
                content_html TEXT,
                content_text TEXT,
                media_url TEXT,
                published_at TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );
            """
        )

        try:
            conn.execute("ALTER TABLE statements ADD COLUMN media_url TEXT;")
        except sqlite3.OperationalError:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS indicators (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                series_id TEXT NOT NULL,
                frequency TEXT NOT NULL,
                source TEXT NOT NULL,
                unit TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS indicator_points (
                indicator_id TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL,
                ingested_at TEXT NOT NULL,
                PRIMARY KEY (indicator_id, date),
                FOREIGN KEY (indicator_id) REFERENCES indicators(id)
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intraday_points (
                series_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                value REAL,
                source TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                PRIMARY KEY (series_id, ts_utc)
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS link_previews (
                url TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                image_url TEXT,
                site_name TEXT,
                status TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )

        conn.commit()

    print(f"Initialized DB at {DB_PATH}")


if __name__ == "__main__":
    main()
