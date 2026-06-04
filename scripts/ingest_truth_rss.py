from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser

from db import connect, iso_utc
from link_preview import extract_links

RSS_URL = "https://trumpstruth.org/feed"
USER_AGENT = "Mozilla/5.0 (compatible; MVPRSS/1.0)"
SOURCE_ID = "trump_truth_rss"



def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = parser.parse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def find_entry_value(entry: feedparser.FeedParserDict, token: str) -> str | None:
    token_lower = token.lower()
    for key in entry.keys():
        if token_lower in key.lower():
            value = entry.get(key)
            if value:
                return value
    return None


def normalize_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return soup.get_text(" ", strip=True)


def is_valid_media_url(url: str) -> bool:
    return "/attachments/" in url or "/media_attachments/" in url


def extract_first_image(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src and is_valid_media_url(src):
            return src
    return None


def score_media_url(url: str) -> int:
    score = 0
    if "/attachments/" in url:
        score += 4
    if "/media_attachments/" in url:
        score += 3
    if "/small/" in url:
        score -= 2
    if "truth-archive" in url:
        score += 1
    return score


def extract_media_from_status_page(status_url: str) -> str | None:
    try:
        resp = requests.get(
            status_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    candidates: list[str] = []

    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        candidates.append(og_image["content"].strip())

    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src:
            candidates.append(src)

    if not candidates:
        return None

    candidates = [url for url in candidates if url.startswith("http")]
    candidates = [url for url in candidates if is_valid_media_url(url)]
    if not candidates:
        return None

    return max(candidates, key=score_media_url)


def ensure_source(conn, now_iso: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sources (id, name, type, base_url, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (SOURCE_ID, "Trump's Truth RSS", "rss", "https://trumpstruth.org", now_iso),
    )


def main() -> None:
    resp = requests.get(RSS_URL, timeout=30)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    now_iso = iso_utc(datetime.now(timezone.utc))
    inserted = 0
    skipped = 0

    with connect() as conn:
        ensure_source(conn, now_iso)

        for entry in feed.entries:
            original_url = find_entry_value(entry, "originalurl")
            original_id = find_entry_value(entry, "originalid")
            guid = entry.get("id") or entry.get("guid") or entry.get("link")

            if original_id:
                statement_id = f"truth:{original_id}"
            elif guid:
                statement_id = f"rss:{guid}"
            else:
                statement_id = f"rss:{hash(entry.get('title', ''))}"

            source_url = entry.get("link") or guid or original_url or ""

            published_at = iso_utc(parse_datetime(entry.get("published")))
            content_html = entry.get("description") or entry.get("summary") or ""
            content_text = normalize_text(content_html)
            media_url = extract_first_image(content_html)
            if not media_url and source_url:
                media_url = extract_media_from_status_page(source_url)
            title = entry.get("title") or None
            if not content_text and title:
                content_text = title

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO statements (
                    id,
                    source_id,
                    source_url,
                    original_id,
                    original_url,
                    title,
                    content_html,
                    content_text,
                    media_url,
                    published_at,
                    ingested_at,
                    verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    statement_id,
                    SOURCE_ID,
                    source_url,
                    original_id,
                    original_url,
                    title,
                    content_html,
                    content_text,
                    media_url,
                    published_at,
                    now_iso,
                ),
            )

            if cur.rowcount == 0:
                existing = conn.execute(
                    "SELECT media_url FROM statements WHERE id = ?",
                    (statement_id,),
                ).fetchone()
                existing_url = existing[0] if existing else None

                if media_url:
                    if not existing_url or "social_previews" in existing_url:
                        conn.execute(
                            """
                            UPDATE statements
                            SET media_url = ?
                            WHERE id = ?
                            """,
                            (media_url, statement_id),
                        )
                else:
                    if existing_url and "social_previews" in existing_url:
                        conn.execute(
                            """
                            UPDATE statements
                            SET media_url = NULL
                            WHERE id = ?
                            """,
                            (statement_id,),
                        )

            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

            for url in extract_links(content_html):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO link_previews (
                        url, title, description, image_url, site_name, status, fetched_at
                    ) VALUES (?, NULL, NULL, NULL, NULL, 'pending', ?)
                    """,
                    (url, now_iso),
                )

        conn.commit()

    print(f"Truth RSS ingestion complete. Inserted={inserted} Skipped={skipped}")


if __name__ == "__main__":
    main()
