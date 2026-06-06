from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from bs4 import BeautifulSoup

from db import connect, iso_utc

app = Flask(__name__, template_folder="../templates", static_folder="../static")

ET_ZONE = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)
MARKET_OPEN = time(9, 30)

REACTION_SERIES = [
    "SP500",
    "NASDAQCOM",
    "DJIA",
    "DTWEXBGS",
    "DEXUSEU",
    "DEXJPUS",
    "DCOILWTICO",
    "DCOILBRENTEU",
    "DHHNGSP",
    "GASREGCOVW",
    "BTCUSD",
    "ETHUSD",
]

REACTION_ORDER = {series_id: index for index, series_id in enumerate(REACTION_SERIES)}


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


def sanitize_statement_html(value: str | None) -> str:
    soup = BeautifulSoup(value or "", "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if href:
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

    body = soup.body or soup
    return "".join(str(child) for child in body.contents).strip()


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

        previews = conn.execute(
            """
            SELECT url, title, description, image_url, site_name, status
            FROM link_previews
            WHERE status = 'ok'
            """
        ).fetchall()

    preview_map = {row["url"]: dict(row) for row in previews}

    payload = []
    for row in rows:
        html = sanitize_statement_html(row["content_html"])
        soup = BeautifulSoup(html or "", "html.parser")
        urls = []
        preview_urls = set(preview_map.keys())
        for anchor in soup.find_all("a"):
            href = (anchor.get("href") or "").strip()
            if not href:
                anchor.unwrap()
                continue
            if href not in urls:
                urls.append(href)
            if href in preview_urls:
                anchor.decompose()

        payload.append(
            {
                "published_at": row["published_at"],
                "id": row["id"],
                "title": row["title"],
                "content_text": row["content_text"],
                "content_html": str(soup),
                "media_url": row["media_url"],
                "source_url": row["source_url"],
                "original_url": row["original_url"],
                "link_previews": [preview_map.get(url) for url in urls if url in preview_map],
            }
        )

    return jsonify(payload)


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
        indicator = conn.execute(
            """
            SELECT id, name, series_id, unit
            FROM indicators
            WHERE series_id = 'VIXCLS'
            """
        ).fetchone()

        if not indicator:
            return jsonify({"points": []})

        points = conn.execute(
            """
            SELECT date, value
            FROM indicator_points
            WHERE indicator_id = ? AND date >= ?
            ORDER BY date ASC
            """,
            (indicator["id"], since_date),
        ).fetchall()

    return jsonify(
        {
            "name": indicator["name"],
            "series_id": indicator["series_id"],
            "unit": indicator["unit"],
            "points": [{"date": row["date"], "value": row["value"]} for row in points],
        }
    )


@app.get("/api/reactions/<statement_id>")
def reactions(statement_id: str):
    with connect() as conn:
        intraday_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_points'"
        ).fetchone()
        has_intraday = bool(intraday_table)

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
            """,
            tuple(REACTION_SERIES),
        ).fetchall()

        indicator_rows = sorted(
            indicators,
            key=lambda row: REACTION_ORDER.get(row["series_id"], 999),
        )

        # Batch fetch all intraday points for all series in one query.
        intraday_by_series: dict[str, list[tuple[str, float]]] = {}
        if has_intraday:
            all_points = conn.execute(
                f"""
                SELECT series_id, ts_utc, value
                FROM intraday_points
                WHERE series_id IN ({placeholders})
                ORDER BY series_id, ts_utc
                """,
                tuple(REACTION_SERIES),
            ).fetchall()
            for row in all_points:
                sid = row["series_id"]
                intraday_by_series.setdefault(sid, []).append((row["ts_utc"], row["value"]))

        reactions_payload = []
        for indicator in indicator_rows:
            series_id = indicator["series_id"]

            if not has_intraday:
                reactions_payload.append(
                    {
                        "series_id": series_id,
                        "name": indicator["name"],
                        "unit": indicator["unit"],
                        "window_10h_pct": None,
                        "window_10h_status": "missing",
                        "since_pre_pct": None,
                        "since_pre_status": "missing",
                        "pre_ts": None,
                        "t0_ts": None,
                        "t10_ts": None,
                        "latest_ts": None,
                    }
                )
                continue

            points = intraday_by_series.get(series_id, [])

            def find_nearest_before(ts: str) -> tuple[str, float] | None:
                result = None
                for p_ts, p_val in points:
                    if p_ts <= ts:
                        result = (p_ts, p_val)
                    else:
                        break
                return result

            def find_nearest_after(ts: str) -> tuple[str, float] | None:
                for p_ts, p_val in points:
                    if p_ts >= ts:
                        return (p_ts, p_val)
                return None

            def find_latest() -> tuple[str, float] | None:
                return points[-1] if points else None

            pre = find_nearest_before(published_utc)
            t0 = find_nearest_after(start_utc)
            t10 = find_nearest_before(end_utc)
            latest = find_latest()

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


_refresh_status = {"running": False, "last_run": None, "results": []}


@app.get("/api/refresh")
@app.post("/api/refresh")
def refresh():
    import os
    import subprocess
    import threading

    if _refresh_status["running"]:
        return jsonify({"status": "already running", "results": _refresh_status["results"]}), 202

    def _run():
        _refresh_status["running"] = True
        env = os.environ.copy()
        project_root = os.path.dirname(os.path.dirname(__file__))
        scripts_dir = os.path.join(project_root, "scripts")
        env["PYTHONPATH"] = scripts_dir

        results = []
        steps = [
            ("init_db", ["python", "scripts/init_db.py"], 30),
            ("rss", ["python", "scripts/ingest_truth_rss.py"], 120),
            ("intraday", ["python", "scripts/ingest_intraday_alpha_vantage.py"], 300),
            ("fred", ["python", "scripts/ingest_fred.py"], 120),
            ("link_previews", ["python", "scripts/fetch_link_previews.py", "--limit", "30"], 120),
        ]

        for name, cmd, timeout in steps:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=project_root, env=env)
                status = "ok" if r.returncode == 0 else "error"
                results.append({
                    "step": name,
                    "status": status,
                    "returncode": r.returncode,
                    "stdout": r.stdout[-500:] if r.stdout else "",
                    "stderr": r.stderr[-500:] if r.stderr else "",
                })
            except subprocess.TimeoutExpired:
                results.append({"step": name, "status": "timeout", "returncode": -1})
            except Exception as e:
                results.append({"step": name, "status": "exception", "error": str(e)})

        _refresh_status["results"] = results
        _refresh_status["last_run"] = now_iso()
        _refresh_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"}), 202


@app.get("/api/refresh/status")
def refresh_status():
    return jsonify(_refresh_status)


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
