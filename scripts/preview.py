from __future__ import annotations

from db import connect


def format_text(value: str | None, limit: int = 240) -> str:
    text = (value or "").strip()
    if not text:
        return "[no content]"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def main() -> None:
    with connect() as conn:
        stat_count = conn.execute("SELECT COUNT(*) AS count FROM statements").fetchone()[
            "count"
        ]
        ind_count = conn.execute("SELECT COUNT(*) AS count FROM indicators").fetchone()[
            "count"
        ]

        print(f"Statements: {stat_count}")
        print(f"Indicators: {ind_count}\n")

        rows = conn.execute(
            """
            SELECT published_at, title, content_text, source_url, original_url
            FROM statements
            ORDER BY published_at DESC
            LIMIT 5
            """
        ).fetchall()

        print("Latest statements:")
        if not rows:
            print("  (none)")
        for row in rows:
            text = row["content_text"] or row["title"]
            print(f"- {row['published_at']}: {format_text(text)}")
            print(f"  source: {row['source_url']}")
            if row["original_url"]:
                print(f"  original: {row['original_url']}")

        print("\nLatest indicator points:")
        rows = conn.execute(
            """
            SELECT indicators.name, indicators.series_id, indicator_points.date, indicator_points.value
            FROM indicators
            JOIN indicator_points ON indicator_points.indicator_id = indicators.id
            WHERE indicator_points.date = (
                SELECT MAX(date) FROM indicator_points ip2 WHERE ip2.indicator_id = indicators.id
            )
            ORDER BY indicators.name
            """
        ).fetchall()

        if not rows:
            print("  (none)")
        for row in rows:
            print(
                f"- {row['name']} ({row['series_id']}): {row['date']} = {row['value']}"
            )


if __name__ == "__main__":
    main()
