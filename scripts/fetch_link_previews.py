from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from db import connect, iso_utc
from link_preview import fetch_link_preview

FETCH_WORKERS = 6


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--refresh-days", type=int, default=7)
    args = parser.parse_args()

    refresh_cutoff = iso_utc(datetime.now(timezone.utc) - timedelta(days=args.refresh_days))

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT url, status, fetched_at
            FROM link_previews
            WHERE status = 'pending'
               OR (status = 'ok' AND fetched_at < ?)
               OR (status = 'error' AND fetched_at < ?)
            ORDER BY fetched_at ASC
            LIMIT ?
            """,
            (refresh_cutoff, refresh_cutoff, args.limit),
        ).fetchall()

        if not rows:
            print("No link previews to fetch.")
            return

        # Fetch concurrently; write each result from this thread as it arrives, so
        # finished previews are saved even if the run is cut short.
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            futures = [pool.submit(fetch_link_preview, row["url"]) for row in rows]
            for future in as_completed(futures):
                preview = future.result()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO link_previews (
                        url, title, description, image_url, site_name, status, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preview.url,
                        preview.title,
                        preview.description,
                        preview.image_url,
                        preview.site_name,
                        preview.status,
                        preview.fetched_at,
                    ),
                )
                conn.commit()
                print(f"Fetched preview: {preview.url} [{preview.status}]")


if __name__ == "__main__":
    main()
