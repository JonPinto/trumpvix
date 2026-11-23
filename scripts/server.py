from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from time import monotonic
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from bs4 import BeautifulSoup

from db import connect, iso_utc, now_iso
import commons_background
import media_cache
from series import REACTION_SERIES

app = Flask(__name__, template_folder="../templates", static_folder="../static")
log = logging.getLogger("trumpvix")

ET_ZONE = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
MARKET_OPEN = time(9, 30)

REACTION_ORDER = {series_id: index for index, series_id in enumerate(REACTION_SERIES)}

# Links in post HTML keep only these schemes; anything else (e.g. javascript:) is unwrapped.
SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")


@app.after_request
def add_security_headers(response):
    """Baseline hardening for every response.

    The page is a single inline script that only calls this origin; post media and
    backgrounds come from https CDNs and blob: URLs. `unsafe-inline` for scripts is a
    deliberate trade-off: the frontend is one inline block, and untrusted content is
    sanitized and escaped before it reaches the DOM.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data: blob:; "
        "media-src 'self' https: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


def parse_days(value: str | None) -> int:
    try:
        days = int(value) if value else 30
    except ValueError:
        days = 30
    return max(1, min(days, 365))




def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def reaction_window(published_at: str) -> tuple[str, str]:
    local_time = parse_iso(published_at).astimezone(ET_ZONE)
    start_date = local_time.date()
    if local_time.time() > MARKET_CLOSE:
        start_date = start_date + timedelta(days=1)
    end_date = start_date + timedelta(days=2)
    return start_date.isoformat(), end_date.isoformat()


def is_weekend(d: datetime.date) -> bool:
    return d.weekday() >= 5


def next_weekday(d: datetime.date) -> datetime.date:
    d2 = d
    while is_weekend(d2):
        d2 = d2 + timedelta(days=1)
    return d2


def next_trading_day(d: datetime.date) -> datetime.date:
    return next_weekday(d + timedelta(days=1))


def market_start_time(local_dt: datetime) -> datetime:
    """Return the datetime (ET) when we start counting market reaction time."""
    day = local_dt.date()
    if is_weekend(day):
        day = next_weekday(day)
        return datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)

    if local_dt.time() < MARKET_OPEN:
        return datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)
    if local_dt.time() > MARKET_CLOSE:
        day = next_trading_day(day)
        return datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)
    return local_dt


def add_market_hours(start_local: datetime, hours: float) -> datetime:
    """Add market-open hours (ET) across sessions; ignores holidays."""
    remaining = hours
    cur = start_local
    while remaining > 0:
        day = cur.date()
        if is_weekend(day):
            day = next_weekday(day)
            cur = datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)

        session_open = datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)
        session_close = datetime.combine(day, MARKET_CLOSE, tzinfo=ET_ZONE)
        if cur < session_open:
            cur = session_open
        if cur >= session_close:
            day = next_trading_day(day)
            cur = datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)
            continue

        available = (session_close - cur).total_seconds() / 3600
        take = min(remaining, available)
        cur = cur + timedelta(seconds=int(take * 3600))
        remaining -= take
        if remaining > 0 and cur >= session_close:
            day = next_trading_day(day)
            cur = datetime.combine(day, MARKET_OPEN, tzinfo=ET_ZONE)
    return cur


def percent_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return (end / start - 1) * 100


# Single-row lookups on the (series_id, ts_utc) primary key, so their cost doesn't grow
# with the amount of stored history.
def point_at_or_before(conn, series_id: str, ts: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT ts_utc, value FROM intraday_points WHERE series_id = ? AND ts_utc <= ? ORDER BY ts_utc DESC LIMIT 1",
        (series_id, ts),
    ).fetchone()
    return (row["ts_utc"], row["value"]) if row else None


def point_at_or_after(conn, series_id: str, ts: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT ts_utc, value FROM intraday_points WHERE series_id = ? AND ts_utc >= ? ORDER BY ts_utc ASC LIMIT 1",
        (series_id, ts),
    ).fetchone()
    return (row["ts_utc"], row["value"]) if row else None


def latest_point(conn, series_id: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT ts_utc, value FROM intraday_points WHERE series_id = ? ORDER BY ts_utc DESC LIMIT 1",
        (series_id,),
    ).fetchone()
    return (row["ts_utc"], row["value"]) if row else None


def _sanitized_soup(value: str | None) -> BeautifulSoup:
    soup = BeautifulSoup(value or "", "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith(SAFE_LINK_SCHEMES):
            anchor.attrs = {
                "href": href,
                "target": "_blank",
                "rel": "noreferrer",
            }
        else:
            anchor.unwrap()

    allowed = {"p", "br", "a"}
    for tag in soup.find_all(True):
        if tag.name not in allowed:
            tag.unwrap()
        elif tag.name != "a":
            # p and br need no attributes; drop any that arrived with the post
            # HTML (event handlers, inline styles).
            tag.attrs = {}

    return soup


def _render(soup: BeautifulSoup) -> str:
    body = soup.body or soup
    return "".join(str(child) for child in body.contents).strip()


# Stored statement HTML never changes, so parsing results are cached per process
# instead of re-running BeautifulSoup for every post on every page load.
@lru_cache(maxsize=4096)
def prepare_statement(content_html: str) -> tuple[str, tuple[str, ...]]:
    """Sanitized HTML plus the unique link targets in it, in order of appearance."""
    soup = _sanitized_soup(content_html)
    links: list[str] = []
    for anchor in soup.find_all("a"):
        href = anchor["href"]
        if href not in links:
            links.append(href)
    return _render(soup), tuple(links)


@lru_cache(maxsize=4096)
def strip_links(clean_html: str, urls: frozenset[str]) -> str:
    """Remove links whose target is shown elsewhere on the card (preview card or media grid).

    A paragraph left empty by the removal is dropped too, so no blank gap remains.
    """
    soup = BeautifulSoup(clean_html, "html.parser")
    for anchor in soup.find_all("a"):
        if anchor.get("href") in urls:
            parent = anchor.parent
            anchor.decompose()
            if parent is not None and parent.name == "p" and not parent.get_text(strip=True) \
                    and not parent.find(lambda tag: tag.name != "br"):
                parent.decompose()
    return _render(soup)


@app.get("/")
def index():
    days = parse_days(request.args.get("days"))
    return render_template("index.html", days=days)


@app.get("/api/statements")
def statements():
    date = (request.args.get("date") or "").strip()
    q = (request.args.get("q") or "").strip()
    days = parse_days(request.args.get("days"))

    base = """
        SELECT id, published_at, title, content_html, content_text, media_url, source_url, original_url
        FROM statements
    """
    where_clauses: list[str] = []
    params: list[str] = []

    if date:
        try:
            day = datetime.fromisoformat(date).date()
        except ValueError:
            day = None
        if day:
            start = iso_utc(datetime.combine(day, time(0, 0), tzinfo=timezone.utc))
            end = iso_utc(
                datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
                + timedelta(days=1)
            )
            where_clauses.append("published_at >= ?")
            params.append(start)
            where_clauses.append("published_at < ?")
            params.append(end)
        else:
            since = iso_utc(datetime.now(timezone.utc) - timedelta(days=days))
            where_clauses.append("published_at >= ?")
            params.append(since)
    else:
        since = iso_utc(datetime.now(timezone.utc) - timedelta(days=days))
        where_clauses.append("published_at >= ?")
        params.append(since)

    if q:
        like = f"%{q}%"
        where_clauses.append("(content_text LIKE ? OR title LIKE ?)")
        params.extend([like, like])

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"{base}{where_sql} ORDER BY published_at DESC LIMIT 30"

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
        prepared = [prepare_statement(row["content_html"] or "") for row in rows]

        # Only the previews for links in the posts being returned.
        all_links = sorted({url for _, links in prepared for url in links})
        previews = conn.execute(
            f"""
            SELECT url, title, description, image_url, site_name, status
            FROM link_previews
            WHERE status = 'ok' AND url IN ({",".join("?" * len(all_links))})
            """,
            all_links,
        ).fetchall() if all_links else []

        ids = [row["id"] for row in rows]
        media_rows = conn.execute(
            f"""
            SELECT statement_id, position, kind, url, poster_url
            FROM statement_media
            WHERE statement_id IN ({",".join("?" * len(ids))})
            ORDER BY statement_id, position
            """,
            ids,
        ).fetchall() if ids else []

    preview_map = {row["url"]: dict(row) for row in previews}
    media_by_statement: dict[str, list[dict]] = {}
    for m in media_rows:
        media_by_statement.setdefault(m["statement_id"], []).append(
            {"position": m["position"], "kind": m["kind"], "url": m["url"], "poster_url": m["poster_url"]}
        )

    payload = []
    for row, (clean_html, links) in zip(rows, prepared):
        media = media_by_statement.get(row["id"])
        if not media and row["media_url"]:
            # Posts ingested before statement_media existed only have their first image.
            media = [{"position": None, "kind": "image", "url": row["media_url"], "poster_url": None}]
        media = media or []

        # Links shown as a preview card or in the media grid are removed from the text.
        media_urls = {m["url"] for m in media}
        hidden = frozenset(url for url in links if url in preview_map or url in media_urls)
        payload.append(
            {
                "published_at": row["published_at"],
                "id": row["id"],
                "title": row["title"],
                "content_text": row["content_text"],
                "content_html": strip_links(clean_html, hidden) if hidden else clean_html,
                "media_url": row["media_url"],
                "media": media,
                "source_url": row["source_url"],
                "original_url": row["original_url"],
                "link_previews": [preview_map[url] for url in links if url in preview_map],
            }
        )

    return jsonify(payload)


@app.get("/api/video")
def video():
    """Proxy a post's video through the per-run cache. Only URLs stored for that post are served."""
    statement_id = request.args.get("statement", "")
    position = request.args.get("position", type=int)
    with connect() as conn:
        row = conn.execute(
            "SELECT url FROM statement_media WHERE statement_id = ? AND position = ? AND kind = 'video'",
            (statement_id, position),
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return media_cache.video_response(row["url"])


@app.get("/api/background")
def background():
    """A random Wikimedia Commons photo of Trump, for the page background."""
    orientation = request.args.get("orientation", "landscape")
    photo = commons_background.random_background(orientation)
    if not photo:
        return jsonify({"error": "no background available"}), 503
    response = jsonify(photo)
    # A new photo is picked for every page load, so never let the browser reuse one.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/indicators")
def indicators():
    days = parse_days(request.args.get("days"))
    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    with connect() as conn:
        indicators = conn.execute(
            """
            SELECT id, name, series_id, unit
            FROM indicators
            ORDER BY name
            """
        ).fetchall()

        payload = []
        for indicator in indicators:
            points = conn.execute(
                """
                SELECT date, value
                FROM indicator_points
                WHERE indicator_id = ? AND date >= ?
                ORDER BY date ASC
                """,
                (indicator["id"], since_date),
            ).fetchall()
            payload.append(
                {
                    "id": indicator["id"],
                    "name": indicator["name"],
                    "series_id": indicator["series_id"],
                    "unit": indicator["unit"],
                    "points": [
                        {"date": row["date"], "value": row["value"]}
                        for row in points
                    ],
                }
            )

    return jsonify(payload)


@app.get("/api/vix")
def vix():
    days = parse_days(request.args.get("days"))
    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    with connect() as conn:
        # Both FRED and the intraday ingester register a VIXCLS indicator; only FRED's has daily VIX points.
        indicator = conn.execute(
            """
            SELECT id, name, series_id, unit
            FROM indicators
            WHERE series_id = 'VIXCLS' AND source = 'FRED'
            """
        ).fetchone()

        daily_points: list[dict] = []
        if indicator:
            daily_points = [
                {"date": row["date"], "value": row["value"]}
                for row in conn.execute(
                    """
                    SELECT date, value
                    FROM indicator_points
                    WHERE indicator_id = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (indicator["id"], since_date),
                ).fetchall()
            ]

        # Intraday ^VIX (the index itself). FRED publishes the daily close a day late,
        # so this keeps the newest value current.
        intraday_rows = conn.execute(
            """
            SELECT ts_utc, value
            FROM intraday_points
            WHERE series_id = 'VIXCLS' AND ts_utc >= ?
            ORDER BY ts_utc ASC
            """,
            (since_date,),
        ).fetchall()

    # Collapse the intraday bars to one value per day: the last bar of that day.
    intraday_by_day: dict[str, float] = {}
    for row in intraday_rows:
        intraday_by_day[row["ts_utc"][:10]] = row["value"]

    if daily_points:
        # Extend FRED's daily line with any intraday day newer than its last close, so the
        # header never shows a stale value while FRED lags behind.
        last_daily = daily_points[-1]["date"]
        for day in sorted(d for d in intraday_by_day if d > last_daily):
            daily_points.append({"date": day, "value": intraday_by_day[day]})
        return jsonify(
            {
                "name": indicator["name"],
                "series_id": indicator["series_id"],
                "unit": indicator["unit"],
                "points": daily_points,
            }
        )

    # No FRED history yet: show the intraday index on its own.
    points = [{"date": day, "value": value} for day, value in sorted(intraday_by_day.items())]
    return jsonify(
        {
            "name": "VIX",
            "series_id": "VIXCLS",
            "unit": "index",
            "points": points,
        }
    )


@app.get("/api/reactions/<path:statement_id>")
def reactions(statement_id: str):
    with connect() as conn:
        statement = conn.execute(
            "SELECT id, published_at FROM statements WHERE id = ?",
            (statement_id,),
        ).fetchone()

        if not statement:
            return jsonify({"error": "not found"}), 404

        published = parse_iso(statement["published_at"]).astimezone(ET_ZONE)
        start_date, end_date = reaction_window(statement["published_at"])

        start_local = market_start_time(published)
        end_local = add_market_hours(start_local, 10.0)
        published_utc = iso_utc(published)
        start_utc = iso_utc(start_local)
        end_utc = iso_utc(end_local)
        now_utc = iso_utc(datetime.now(timezone.utc))

        placeholders = ",".join(["?"] * len(REACTION_SERIES))
        indicators = conn.execute(
            f"""
            SELECT id, name, series_id, unit
            FROM indicators
            WHERE series_id IN ({placeholders})
            ORDER BY series_id, source = 'FRED' DESC
            """,
            tuple(REACTION_SERIES),
        ).fetchall()

        # A series can have both a FRED and an intraday indicator row. Prefer FRED's:
        # the daily fallback below looks up indicator_points by its id.
        indicator_by_series: dict = {}
        for row in indicators:
            indicator_by_series.setdefault(row["series_id"], row)

        indicator_rows = sorted(
            indicator_by_series.values(),
            key=lambda row: REACTION_ORDER.get(row["series_id"], 999),
        )

        reactions_payload = []
        for indicator in indicator_rows:
            series_id = indicator["series_id"]

            pre = point_at_or_before(conn, series_id, published_utc)
            t0 = point_at_or_after(conn, series_id, start_utc)
            t10 = point_at_or_before(conn, series_id, end_utc)
            latest = latest_point(conn, series_id)

            has_intraday_points = bool(pre or t0 or t10 or latest)

            window_status = "missing"
            window_pct = None
            if not t0 and start_utc > now_utc:
                window_status = "closed"
            elif t0 and latest and latest[0] < end_utc:
                window_status = "partial"
                if latest[0] > t0[0]:
                    window_pct = percent_change(t0[1], latest[1])
            elif t0 and t10 and t10[0] > t0[0]:
                window_status = "ok"
                window_pct = percent_change(t0[1], t10[1])

            since_status = "missing"
            since_pct = None
            if not pre and not latest:
                since_status = "missing"
            elif latest and pre and latest[0] <= pre[0]:
                since_status = "closed" if published_utc < start_utc and start_utc > now_utc else "missing"
            elif pre and latest and latest[0] > pre[0]:
                since_status = "ok"
                since_pct = percent_change(pre[1], latest[1])

            # Fallback to daily points if no intraday exists for this series.
            if not has_intraday_points:
                t0d = conn.execute(
                    """
                    SELECT date, value
                    FROM indicator_points
                    WHERE indicator_id = ? AND date >= ?
                    ORDER BY date ASC
                    LIMIT 1
                    """,
                    (indicator["id"], start_date),
                ).fetchone()
                t2d = conn.execute(
                    """
                    SELECT date, value
                    FROM indicator_points
                    WHERE indicator_id = ? AND date >= ? AND date <= ?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (indicator["id"], start_date, end_date),
                ).fetchone()
                latestd = conn.execute(
                    """
                    SELECT date, value
                    FROM indicator_points
                    WHERE indicator_id = ?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (indicator["id"],),
                ).fetchone()

                window_pct = (
                    percent_change(t0d["value"], t2d["value"])
                    if t0d and t2d and t2d["date"] > t0d["date"]
                    else None
                )
                since_pct = (
                    percent_change(t0d["value"], latestd["value"])
                    if t0d and latestd and latestd["date"] > t0d["date"]
                    else None
                )
                window_status = "daily"
                since_status = "daily"

            reactions_payload.append(
                {
                    "series_id": series_id,
                    "name": indicator["name"],
                    "unit": indicator["unit"],
                    "window_10h_pct": window_pct,
                    "window_10h_status": window_status,
                    "since_pre_pct": since_pct,
                    "since_pre_status": since_status,
                    "pre_ts": pre[0] if pre else None,
                    "t0_ts": t0[0] if t0 else None,
                    "t10_ts": t10[0] if t10 else None,
                    "latest_ts": latest[0] if latest else None,
                }
            )

    return jsonify(
        {
            "statement_id": statement["id"],
            "start_date": start_date,
            "end_date": end_date,
            "market_start_ts": start_utc,
            "market_end_10h_ts": end_utc,
            "reactions": reactions_payload,
        }
    )


REFRESH_STEPS = [
    ("init_db", ["scripts/init_db.py"], 30),
    ("rss", ["scripts/ingest_truth_rss.py"], 120),
    ("intraday", ["scripts/ingest_intraday_yfinance.py"], 300),
    ("fred", ["scripts/ingest_fred.py"], 300),
    ("link_previews", ["scripts/fetch_link_previews.py", "--limit", "30"], 120),
]

# Shared between request threads and the refresh thread; guarded by _refresh_lock.
_refresh_lock = threading.Lock()
_refresh_status = {
    "running": False,
    "started_at": None,
    "current_step": None,
    "last_run": None,
    "results": [],
}

# Refresh is a local control. By default only the machine running the server may
# trigger one; set REFRESH_ALLOW_REMOTE=1 to let other hosts (still origin-checked
# and rate-limited). REFRESH_MIN_INTERVAL throttles HTTP-triggered refreshes.
REFRESH_ALLOW_REMOTE = os.environ.get("REFRESH_ALLOW_REMOTE", "0") == "1"
try:
    REFRESH_MIN_INTERVAL = max(0, int(os.environ.get("REFRESH_MIN_INTERVAL", "300")))
except ValueError:
    REFRESH_MIN_INTERVAL = 300
_last_http_refresh = 0.0


def client_is_local() -> bool:
    """True when the request comes from this machine, not through a proxy.

    Behind a same-host reverse proxy every request looks local, so a forwarded
    header means it actually came from somewhere else.
    """
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return False
    return not request.headers.get("X-Forwarded-For")


def refresh_is_same_origin() -> bool:
    """Reject cross-site refresh requests, which need no CORS preflight (CSRF)."""
    site = request.headers.get("Sec-Fetch-Site")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("Origin")
    if origin:
        host = request.host
        if origin not in (f"http://{host}", f"https://{host}"):
            return False
    return True


def refresh_allowed_here() -> bool:
    return REFRESH_ALLOW_REMOTE or client_is_local()


def refresh_rate_gate() -> tuple[bool, int]:
    """Return (allowed, retry_after_s) for an HTTP-triggered refresh."""
    if REFRESH_MIN_INTERVAL <= 0:
        return True, 0
    with _refresh_lock:
        last = _last_http_refresh
    if last:
        elapsed = monotonic() - last
        if elapsed < REFRESH_MIN_INTERVAL:
            return False, int(REFRESH_MIN_INTERVAL - elapsed) + 1
    return True, 0


def _run_step(name: str, args: list[str], timeout: int, project_root: str, env: dict) -> dict:
    try:
        r = subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_root,
            env=env,
        )
        return {
            "step": name,
            "status": "ok" if r.returncode == 0 else "error",
            "returncode": r.returncode,
            "stdout": r.stdout[-500:] if r.stdout else "",
            "stderr": r.stderr[-500:] if r.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"step": name, "status": "timeout", "returncode": -1}
    except Exception as e:
        return {"step": name, "status": "exception", "error": str(e)}


def _run_refresh() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": os.path.join(project_root, "scripts")}
    log.info("Refresh started")
    try:
        for name, args, timeout in REFRESH_STEPS:
            with _refresh_lock:
                _refresh_status["current_step"] = name
            started = monotonic()
            result = _run_step(name, args, timeout, project_root, env)
            result["duration_s"] = round(monotonic() - started, 1)
            if result["status"] == "ok":
                log.info("Refresh step %s: ok (%.1fs)", name, result["duration_s"])
            else:
                detail = result.get("stderr") or result.get("error") or ""
                log.warning("Refresh step %s: %s (%.1fs) %s", name, result["status"], result["duration_s"], detail.strip()[-300:])
            # Publish each step as it finishes so the page can reload data as each source lands.
            with _refresh_lock:
                _refresh_status["results"].append(result)
        with _refresh_lock:
            _refresh_status["last_run"] = now_iso()
        log.info("Refresh finished")
    finally:
        with _refresh_lock:
            _refresh_status["running"] = False
            _refresh_status["current_step"] = None


def start_refresh() -> bool:
    """Start a background refresh. Returns False if one is already running."""
    with _refresh_lock:
        if _refresh_status["running"]:
            return False
        _refresh_status.update(running=True, started_at=now_iso(), current_step=None, results=[])
    threading.Thread(target=_run_refresh, daemon=True).start()
    return True


def refresh_status_snapshot() -> dict:
    with _refresh_lock:
        return {**_refresh_status, "results": list(_refresh_status["results"])}


@app.post("/api/refresh")
def refresh():
    if not refresh_is_same_origin():
        return jsonify({"error": "cross-origin refresh rejected"}), 403
    if not refresh_allowed_here():
        return jsonify({"error": "refresh is limited to the server machine"}), 403
    allowed, retry_after = refresh_rate_gate()
    if not allowed:
        return jsonify({"status": "rate limited", "retry_after_s": retry_after}), 429
    if not start_refresh():
        return jsonify({"status": "already running", **refresh_status_snapshot()}), 202
    global _last_http_refresh
    with _refresh_lock:
        _last_http_refresh = monotonic()
    return jsonify({"status": "started"}), 202


@app.get("/api/refresh/status")
def refresh_status():
    payload = refresh_status_snapshot()
    if not client_is_local():
        # Don't hand subprocess output (paths, upstream errors) to other hosts.
        payload["results"] = [
            {key: result[key] for key in ("step", "status", "duration_s") if key in result}
            for result in payload["results"]
        ]
    payload["can_refresh"] = refresh_allowed_here() and refresh_is_same_origin()
    payload["retry_after_s"] = refresh_rate_gate()[1]
    return jsonify(payload)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Exit normally on SIGTERM (e.g. `kill`) so atexit cleanup, such as removing the
    # per-run video cache, still runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    # Serve whatever is already stored right away and fetch fresh data in the background.
    # Set REFRESH_ON_START=0 to skip.
    if os.environ.get("REFRESH_ON_START", "1") != "0":
        start_refresh()

    port = int(os.environ.get("PORT", 5000))
    # Localhost by default; set HOST=0.0.0.0 to serve the LAN (see Security in the README).
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(debug=False, host=host, port=port)
