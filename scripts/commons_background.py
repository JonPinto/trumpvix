"""Random Trump-related background photos from Wikimedia Commons.

Commons is searched for photos of Donald Trump; the results are filtered to real
photographs with a usable size and shape, then cached in memory so a page load
doesn't wait on the API. Each request picks a different photo from the pool.
"""
from __future__ import annotations

import html
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import requests

log = logging.getLogger("trumpvix.backgrounds")

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "trumpvix-local/1.0 (personal dashboard; Wikimedia Commons backgrounds)"
TIMEOUT = (5, 25)

# Several searches give a wider mix of years, settings and photographers than one
# generic search. Each returns up to 50 files.
SEARCH_QUERIES = (
    "Donald Trump filetype:bitmap",
    "Donald Trump 2016 filetype:bitmap",
    "Donald Trump 2020 filetype:bitmap",
    "Donald Trump 2024 filetype:bitmap",
    "Donald Trump inauguration filetype:bitmap",
    "Donald Trump rally filetype:bitmap",
    "Donald Trump White House filetype:bitmap",
    "Donald Trump speaking filetype:bitmap",
    "Donald Trump portrait filetype:bitmap",
    "Donald Trump signing filetype:bitmap",
)

# Files whose titles say they are something other than a photograph of Trump
# (maps, logos, tickets, signs, crowds, ...) make poor backgrounds even when the
# search matches them.
EXCLUDE_TITLE = re.compile(
    r"\b(?:logos?|signatures?|maps?|charts?|graphs?|posters?|books?|covers?|ballots?|"
    r"facebook|twitter|screenshots?|montages?|collages?|photomosaics?|memes?|"
    r"caricatures?|paintings?|statues?|plaques?|flags?|seals?|coins?|stamps?|"
    r"documents?|letters?|emails?|tweets?|truth social|wikipedia|commons|edits?|"
    r"svgs?|icons?|ratings?|polls?|results?|battleground|tickets?|confederate|"
    r"protests?|graffiti|murals?|banners?|stickers?|bumpers?|shirts?|hats?|caps?|"
    r"masks?|dolls?|figures?|wax|sculptures?|drawings?|illustrations?|cartoons?|"
    r"satires?|mugshots?|wanted|magazines?|newspapers?|headlines?|pdf|djvu|tif|tiff|"
    r"webm|ogv|videos?|exteriors?|supporters?|crowds?|waiting|people waiting|"
    r"protesting|photomosaics?|campaign signs?|songs?|trump jr"
    r")\b",
    re.IGNORECASE,
)

# "sign" as a noun (campaign sign, sign with Pence, ...), but not the verb in
# "to sign the Laken Riley Act" and not "signs"/"signing".
EXCLUDE_SIGN = re.compile(r"(?<!to )\bsign\b", re.IGNORECASE)

# A photo of Melania/Ivanka/etc. alone is off-topic; keep it when Donald or the
# President is also named (e.g. "Donald and Melania Trump ...").
FAMILY_ONLY = re.compile(r"\b(?:melania|ivanka|erik|barron|trump jr)\b", re.IGNORECASE)
DONALD_NAMED = re.compile(r"\b(?:donald|president)\b|&", re.IGNORECASE)

ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp")
ORIENTATIONS = ("landscape", "portrait", "any")
LANDSCAPE_WIDTH = 1920
PORTRAIT_WIDTH = 1280  # thumb.wikimedia.org serves this standard size (1080 is rejected)
CACHE_TTL = 6 * 60 * 60
RETRY_AFTER = 5 * 60

_POOL: list[dict] = []
_POOL_EXPIRES = 0.0
_BUILD_LOCK = threading.Lock()
_PICK_LOCK = threading.Lock()
_LAST_URL: dict[str, str] = {}


def _clean_url(value: str) -> str:
    """Keep only an absolute http(s) URL, dropping Commons' tracking query string."""
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _clean_text(value: str) -> str:
    """Turn Commons' small HTML fragments (usually an author link) into plain text."""
    if not value:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", text).strip()


def _fetch_query(query: str) -> list[dict]:
    try:
        response = requests.get(
            API,
            params={
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 50,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": LANDSCAPE_WIDTH,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("query", {}).get("pages", [])
    except (requests.RequestException, ValueError) as err:
        log.warning("Commons search failed for %r: %s", query, err)
        return []


def _usable(width: int, height: int) -> bool:
    if width < 700 or height < 600:
        return False
    return 0.55 <= width / height <= 2.8


def _scaled(width: int, height: int, target_width: int) -> tuple[int, int]:
    served_width = min(width, target_width)
    return served_width, round(height * served_width / width) if width else height


def _smaller_thumb(url: str, width: int) -> str:
    """Ask Commons' thumbnail service for a smaller version of the same photo."""
    if "/thumb/" not in url:
        return url
    return re.sub(r"/\d+px-", f"/{width}px-", url, count=1)


def _build_pool() -> list[dict]:
    # A handful at a time is enough and stays well clear of Commons' rate limit.
    with ThreadPoolExecutor(max_workers=5) as executor:
        page_lists = list(executor.map(_fetch_query, SEARCH_QUERIES))
    pages = [page for page_list in page_lists for page in page_list]

    pool: list[dict] = []
    seen: set[int] = set()
    for page in pages:
        info = (page.get("imageinfo") or [None])[0]
        if not info or page.get("pageid") in seen:
            continue
        title = page.get("title") or ""
        if "trump" not in title.lower() or EXCLUDE_TITLE.search(title) or EXCLUDE_SIGN.search(title):
            continue
        if FAMILY_ONLY.search(title) and not DONALD_NAMED.search(title):
            continue
        if info.get("mime") not in ALLOWED_MIME:
            continue
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if not _usable(width, height):
            continue

        url = _clean_url(info.get("thumburl") or info.get("url") or "")
        page_url = _clean_url(info.get("descriptionurl") or "")
        # Use thumbnails only: originals can be huge, and the full-size CDN is much
        # more aggressive about rate-limiting than the thumbnail service.
        if not url or not page_url or "/thumb/" not in urlsplit(url).path:
            continue

        metadata = info.get("extmetadata") or {}
        artist = _clean_text(metadata.get("Artist", {}).get("value", ""))
        license_name = _clean_text(metadata.get("LicenseShortName", {}).get("value", ""))
        license_url = _clean_url(metadata.get("LicenseUrl", {}).get("value", ""))
        if len(artist) > 120:
            artist = artist[:117].rstrip() + "..."

        landscape_width, landscape_height = _scaled(width, height, LANDSCAPE_WIDTH)
        portrait_width, portrait_height = _scaled(width, height, PORTRAIT_WIDTH)
        seen.add(page["pageid"])
        pool.append(
            {
                "url": url,
                "portrait_url": _smaller_thumb(url, PORTRAIT_WIDTH),
                "title": title.removeprefix("File:"),
                "page_url": page_url,
                "artist": artist,
                "license": license_name,
                "license_url": license_url,
                "original_width": width,
                "original_height": height,
                "ratio": width / height,
                "width": landscape_width,
                "height": landscape_height,
                "portrait_width": portrait_width,
                "portrait_height": portrait_height,
            }
        )

    log.info("Commons background pool: %d photos from %d results", len(pool), len(pages))
    return pool


def _all_pool() -> list[dict]:
    global _POOL, _POOL_EXPIRES
    with _BUILD_LOCK:
        if _POOL and time.time() < _POOL_EXPIRES:
            return _POOL
        _POOL = _build_pool()
        _POOL_EXPIRES = time.time() + (CACHE_TTL if _POOL else RETRY_AFTER)
        return _POOL


def _candidates(pool: list[dict], orientation: str) -> list[dict]:
    if orientation == "landscape":
        return [p for p in pool if p["ratio"] >= 1.4 and p["original_width"] >= 1200]
    if orientation == "portrait":
        return [p for p in pool if p["ratio"] <= 1.05 and p["original_height"] >= 1000]
    return pool


def random_background(orientation: str = "landscape") -> dict | None:
    """A random cached photo, preferring the requested orientation."""
    if orientation not in ORIENTATIONS:
        orientation = "landscape"
    pool = _all_pool()
    if not pool:
        return None

    choices = _candidates(pool, orientation) or pool
    with _PICK_LOCK:
        last = _LAST_URL.get(orientation)
        options = [photo for photo in choices if photo["url"] != last] or choices
        photo = random.choice(options)
        _LAST_URL[orientation] = photo["url"]

    use_portrait = orientation == "portrait" and photo["ratio"] <= 1.05
    return {
        "url": photo["portrait_url"] if use_portrait else photo["url"],
        "width": photo["portrait_width"] if use_portrait else photo["width"],
        "height": photo["portrait_height"] if use_portrait else photo["height"],
        "title": photo["title"],
        "page_url": photo["page_url"],
        "artist": photo["artist"],
        "license": photo["license"],
        "license_url": photo["license_url"],
    }
