from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from db import now_iso

USER_AGENT = "Mozilla/5.0 (compatible; MVPLinkPreview/1.0)"
# Only the <head> metadata is needed; cap the download so large pages can't stall the fetch.
MAX_HTML_BYTES = 2 * 1024 * 1024


@dataclass
class LinkPreview:
    url: str
    title: str | None
    description: str | None
    image_url: str | None
    site_name: str | None
    status: str
    fetched_at: str



def display_url_label(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    return host or url


def extract_links(html: str | None) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("http://") or href.startswith("https://"):
            if href not in links:
                links.append(href)
    return links


def _first_meta_content(soup: BeautifulSoup, attrs: Iterable[dict]) -> str | None:
    for attr in attrs:
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def fetch_link_preview(url: str) -> LinkPreview:
    fetched_at = now_iso()
    try:
        # stream=True fetches headers first, so non-HTML links (videos, images)
        # are rejected without downloading their body.
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=12,
            allow_redirects=True,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type:
                return LinkPreview(
                    url=url,
                    title=None,
                    description=f"Unsupported content-type: {content_type}",
                    image_url=None,
                    site_name=None,
                    status="non_html",
                    fetched_at=fetched_at,
                )

            body = b""
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                body += chunk
                if len(body) >= MAX_HTML_BYTES:
                    break
            final_url = resp.url
    except Exception as exc:  # noqa: BLE001 - store error status
        return LinkPreview(
            url=url,
            title=None,
            description=str(exc),
            image_url=None,
            site_name=None,
            status="error",
            fetched_at=fetched_at,
        )

    soup = BeautifulSoup(body, "html.parser")

    title = _first_meta_content(
        soup,
        (
            {"property": "og:title"},
            {"name": "twitter:title"},
        ),
    )
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    description = _first_meta_content(
        soup,
        (
            {"property": "og:description"},
            {"name": "twitter:description"},
            {"name": "description"},
        ),
    )

    image_url = _first_meta_content(
        soup,
        (
            {"property": "og:image"},
            {"name": "twitter:image"},
        ),
    )
    if image_url:
        image_url = urljoin(final_url, image_url)

    site_name = _first_meta_content(
        soup,
        (
            {"property": "og:site_name"},
            {"name": "twitter:site"},
        ),
    )

    return LinkPreview(
        url=url,
        title=title,
        description=description,
        image_url=image_url,
        site_name=site_name,
        status="ok",
        fetched_at=fetched_at,
    )
