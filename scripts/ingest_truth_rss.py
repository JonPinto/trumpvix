from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser

import net_guard
from db import connect, iso_utc
from link_preview import extract_links, is_media_attachment_url

RSS_URL = "https://trumpstruth.org/feed"
USER_AGENT = "Mozilla/5.0 (compatible; MVPRSS/1.0)"
SOURCE_ID = "trump_truth_rss"
STATUS_PAGE_WORKERS = 8



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


def extract_first_image(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src and is_media_attachment_url(src):
            return src
    return None


def extract_media_from_feed(entry: feedparser.FeedParserDict) -> str | None:
    """First displayable media item from the feed's <media:content> elements.

    Images are used directly. Videos can't be shown in an <img>, so their poster frame
    from the matching <media:thumbnail> is used instead (thumbnails pair 1:1 by index).
    """
    contents = entry.get("media_content") or []
    thumbnails = entry.get("media_thumbnail") or []
    for index, media in enumerate(contents):
        url = media.get("url") or ""
        if media.get("medium") == "image" and is_media_attachment_url(url):
            return url
        if media.get("medium") == "video" and index < len(thumbnails):
            poster = thumbnails[index].get("url") or ""
            if is_media_attachment_url(poster):
                return poster
    return None


def extract_all_media(entry: feedparser.FeedParserDict) -> list[dict]:
    """Every image/video in the feed entry, in order, with its thumbnail (a video's poster frame)."""
    contents = entry.get("media_content") or []
    thumbnails = entry.get("media_thumbnail") or []
    media = []
    for index, item in enumerate(contents):
        kind = item.get("medium")
        url = (item.get("url") or "").strip()
        if kind not in ("image", "video") or not url.startswith(("http://", "https://")):
            continue
        poster = (thumbnails[index].get("url") or "").strip() if index < len(thumbnails) else ""
        media.append({"kind": kind, "url": url, "poster_url": poster or None})
    return media


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
        resp = net_guard.safe_get(
            status_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except (requests.RequestException, net_guard.BlockedURL):
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
    candidates = [url for url in candidates if is_media_attachment_url(url)]
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


def build_record(entry: feedparser.FeedParserDict) -> dict:
    original_url = find_entry_value(entry, "originalurl")
    original_id = find_entry_value(entry, "originalid")
    guid = entry.get("id") or entry.get("guid") or entry.get("link")

    if original_id:
        statement_id = f"truth:{original_id}"
    elif guid:
        statement_id = f"rss:{guid}"
    else:
        statement_id = f"rss:{hashlib.sha256(entry.get('title', '').encode()).hexdigest()}"

    content_html = entry.get("description") or entry.get("summary") or ""
    content_text = normalize_text(content_html)
    title = entry.get("title") or None
    if not content_text and title:
        content_text = title

    return {
        "id": statement_id,
        "source_url": entry.get("link") or guid or original_url or "",
        "original_id": original_id,
        "original_url": original_url,
        "title": title,
        "content_html": content_html,
        "content_text": content_text,
        "media_url": extract_media_from_feed(entry) or extract_first_image(content_html),
        "media": extract_all_media(entry),
        "has_feed_media": bool(entry.get("media_content")),
        "published_at": iso_utc(parse_datetime(entry.get("published"))),
        "links": extract_links(content_html),
    }


def update_existing_media(conn, statement_id: str, existing_url: str | None, media_url: str | None) -> None:
    # Older ingests could store a generic "social_previews" card image; replace or clear it.
    if media_url:
        if not existing_url or "social_previews" in existing_url:
            conn.execute("UPDATE statements SET media_url = ? WHERE id = ?", (media_url, statement_id))
    elif existing_url and "social_previews" in existing_url:
        conn.execute("UPDATE statements SET media_url = NULL WHERE id = ?", (statement_id,))


def main() -> None:
    # All network work happens before the database is opened for writing, so the
    # write transaction at the end is short and doesn't block other writers.
    resp = requests.get(RSS_URL, timeout=30)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    now_iso = iso_utc(datetime.now(timezone.utc))
    records = [build_record(entry) for entry in feed.entries]

    ids = [record["id"] for record in records]
    with connect() as conn:
        placeholders = ",".join("?" * len(ids))
        existing = dict(
            conn.execute(f"SELECT id, media_url FROM statements WHERE id IN ({placeholders})", ids).fetchall()
        ) if ids else {}

    # The feed normally carries every post's media. Fall back to scraping the status page
    # only for new posts whose feed media had no usable URL.
    to_scrape = [
        record for record in records
        if record["id"] not in existing
        and record["media_url"] is None
        and record["has_feed_media"]
        and record["source_url"]
    ]
    if to_scrape:
        with ThreadPoolExecutor(max_workers=STATUS_PAGE_WORKERS) as pool:
            scraped = pool.map(extract_media_from_status_page, [record["source_url"] for record in to_scrape])
            for record, media_url in zip(to_scrape, scraped):
                record["media_url"] = media_url

    inserted = 0
    skipped = 0

    with connect() as conn:
        ensure_source(conn, now_iso)

        for record in records:
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
                    record["id"],
                    SOURCE_ID,
                    record["source_url"],
                    record["original_id"],
                    record["original_url"],
                    record["title"],
                    record["content_html"],
                    record["content_text"],
                    record["media_url"],
                    record["published_at"],
                    now_iso,
                ),
            )

            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
                update_existing_media(conn, record["id"], existing.get(record["id"]), record["media_url"])

            # Replace rather than merge: the feed is the source of truth for a post's
            # attachments, and this also backfills posts stored before this table existed.
            conn.execute("DELETE FROM statement_media WHERE statement_id = ?", (record["id"],))
            conn.executemany(
                """
                INSERT INTO statement_media (statement_id, position, kind, url, poster_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (record["id"], position, media["kind"], media["url"], media["poster_url"])
                    for position, media in enumerate(record["media"])
                ],
            )

            for url in record["links"]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO link_previews (
                        url, title, description, image_url, site_name, status, fetched_at
                    ) VALUES (?, NULL, NULL, NULL, NULL, 'pending', ?)
                    """,
                    (url, now_iso),
                )

        conn.commit()

    print(
        f"Truth RSS ingestion complete. Inserted={inserted} Skipped={skipped} "
        f"StatusPagesFetched={len(to_scrape)}"
    )


if __name__ == "__main__":
    main()
