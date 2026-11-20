#!/usr/bin/env python3
"""Export a static snapshot of the dashboard for the demo site.

The demo is a plain static site: the real frontend plus a baked `snapshot.js`
that answers every `/api/*` call the page makes. This script produces that
directory, ready to be pushed to the demo repository and served by Netlify.

Usage:
    python scripts/export_demo.py [--out dist-demo]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import commons_background  # noqa: E402
from server import app  # noqa: E402

REPO_URL = "https://github.com/JonPinto/trumpvix"
STATEMENT_DAYS = 30
BACKGROUND_POOL_SIZE = 8

BANNER_ANCHOR = (
    '    <div class="bg-layer" id="bgLayer" aria-hidden="true">'
    '<img id="bgPhoto" alt="" /></div>'
)
BANNER_INSERT = (
    BANNER_ANCHOR
    + '\n    <div class="demo-banner" id="demoBanner" role="note"></div>'
)
SCRIPT_ANCHOR = "    <script>\n    (() => {"
SCRIPT_INSERT = (
    '    <script src="/snapshot.js"></script>\n'
    '    <script src="/demo.js"></script>\n'
    + SCRIPT_ANCHOR
)
PLAYVIDEO_ANCHOR = (
    "      async function playVideo(stage, item, index) {\n"
    "        const media = item.media[index];"
)
PLAYVIDEO_INSERT = (
    "      async function playVideo(stage, item, index) {\n"
    "        if (window.__DEMO__) {\n"
    "          // Static snapshot: no server to proxy the video, so open the source.\n"
    "          const source = item.media[index] && item.media[index].url;\n"
    "          const original = source ? safeUrl(source) : \"\";\n"
    "          if (original) window.open(original, \"_blank\", \"noreferrer\");\n"
    "          return;\n"
    "        }\n"
    "        const media = item.media[index];"
)

BANNER_CSS = """
/* ── Demo banner (static snapshot) ── */
.demo-banner {
  position: relative;
  z-index: 5;
  padding: 0.6rem 1rem;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.5;
  text-align: center;
}

.demo-banner strong { color: var(--ink); font-weight: 600; }
.demo-banner .demo-banner-tag { color: var(--interactive); font-weight: 700; }
.demo-banner a { color: var(--interactive); text-decoration: none; }
.demo-banner a:hover { text-decoration: underline; }

@media (max-width: 640px) {
  .demo-banner { font-size: 0.76rem; padding: 0.55rem 0.75rem; }
}
"""

DEMO_JS = """// Static-snapshot shim: answers the dashboard's /api/* calls from snapshot.js.
(() => {
  const snap = window.__SNAPSHOT__;
  if (!snap) return;
  window.__DEMO__ = true;

  const realFetch = window.fetch.bind(window);

  function jsonResponse(data, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }

  function statementsFor(url) {
    const q = url.searchParams.get("q");
    const date = url.searchParams.get("date");
    let items = snap.statements.slice();
    if (q) {
      const needle = q.toLowerCase();
      items = items.filter((s) =>
        (s.content_text || "").toLowerCase().includes(needle) ||
        (s.title || "").toLowerCase().includes(needle));
    } else if (date) {
      items = items.filter((s) => (s.published_at || "").slice(0, 10) === date);
    }
    return items.slice(0, 30);
  }

  window.fetch = function (input, init) {
    const raw = typeof input === "string" ? input : (input && input.url) || "";
    let url;
    try {
      url = new URL(raw, location.href);
    } catch (e) {
      return realFetch(input, init);
    }
    const path = url.pathname;

    if (path === "/api/statements") {
      return Promise.resolve(jsonResponse(statementsFor(url)));
    }
    if (path === "/api/vix") {
      return Promise.resolve(jsonResponse(snap.vix));
    }
    if (path.indexOf("/api/reactions/") === 0) {
      const id = decodeURIComponent(path.slice("/api/reactions/".length));
      const reaction = snap.reactions[id];
      return Promise.resolve(reaction
        ? jsonResponse(reaction)
        : jsonResponse({ error: "not found" }, 404));
    }
    if (path === "/api/background") {
      const orientation = url.searchParams.get("orientation") === "portrait" ? "portrait" : "landscape";
      const pool = (snap.backgrounds && snap.backgrounds[orientation]) || [];
      if (!pool.length) {
        return Promise.resolve(jsonResponse({ error: "no background available" }, 503));
      }
      return Promise.resolve(jsonResponse(pool[Math.floor(Math.random() * pool.length)]));
    }
    if (path === "/api/refresh/status") {
      return Promise.resolve(jsonResponse({
        running: false,
        current_step: null,
        results: [],
        last_run: snap.generated_at,
      }));
    }
    if (path === "/api/refresh") {
      return Promise.resolve(jsonResponse({ ok: true, demo: true }, 202));
    }
    if (path === "/api/video") {
      // No server to proxy through; playVideo opens the source instead.
      return Promise.resolve(jsonResponse({ error: "demo" }, 404));
    }
    return realFetch(input, init);
  };

  function initUi() {
    const banner = document.getElementById("demoBanner");
    if (banner) {
      const link = snap.repo_url || "https://github.com/JonPinto/trumpvix";
      const label = link.replace(/^https?:\\/\\//, "").replace(/\\/$/, "");
      banner.innerHTML =
        '<span class="demo-banner-tag">Demo snapshot ' + snap.app_version + '</span> · ' +
        'snapshot ' + snap.snapshot_version + ' — data up to date as of <strong>' +
        snap.data_as_of + '</strong>. This is a static snapshot; the full app with a ' +
        'live, updating database is at <a href="' + link + '" target="_blank" ' +
        'rel="noreferrer">' + label + '</a>.';
    }
    // The refresh button has nothing to talk to in the snapshot.
    const refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
      const divider = refreshBtn.previousElementSibling;
      if (divider && divider.classList.contains("search-divider")) {
        divider.style.display = "none";
      }
      refreshBtn.style.display = "none";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initUi);
  } else {
    initUi();
  }
})();
"""

NETLIFY_TOML = """[build]
  publish = "."

[[headers]]
  for = "/*"
  [headers.values]
    Cache-Control = "public, max-age=0, must-revalidate"
"""

README_TEMPLATE = """# TrumpVIX — Demo Snapshot

**This is a static, read-only demo snapshot of [TrumpVIX](https://github.com/JonPinto/trumpvix).**

It is **not** the live application. Every post, market figure and reaction shown here was
baked in when this snapshot was generated, and nothing on this site updates. There is no
server, database or API behind it. For the full app — with a live database that refreshes —
see the main repository:

**https://github.com/JonPinto/trumpvix**

| | |
|---|---|
| App version | `{app_version}` |
| Snapshot version | `{snapshot_version}` |
| Data up to date as of | {data_as_of} |
| Generated | {generated_at} |

## Disclaimer

This site is a static snapshot. The data was fetched once, from the same sources the live
app uses — the trumpstruth.org RSS feed, Yahoo Finance (via `yfinance`), FRED, OpenGraph
link previews and Wikimedia Commons — and frozen at the timestamp above. It does not
refresh, and no server or API runs behind this page.

Market data comes from Yahoo Finance via `yfinance` and from FRED. Yahoo data is
unofficial, may be delayed, and can be revised or missing. FRED data is subject to
revision. Nothing here is financial advice.

Wikimedia Commons photos are credited in the page; licences vary by image. Post media is
linked from its original source and may expire.

## What this repository contains

```
index.html      The dashboard, with a demo banner at the top
static/         Stylesheet and logo
snapshot.js     The baked data (window.__SNAPSHOT__)
demo.js         Fetch shim that answers /api/* from snapshot.js
netlify.toml    Static publish config (no build step)
```

This repository is generated. The source of truth, and the export script that produced it,
live in the main [TrumpVIX](https://github.com/JonPinto/trumpvix) repository
(`scripts/export_demo.py`).

## Deployment

Netlify is configured for a static publish from the repository root (`netlify.toml`), with
no build command. Connect this repository in Netlify and it will deploy as-is.
"""


def app_version() -> str:
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return tag or "dev"
    except (subprocess.SubprocessError, OSError):
        return "dev"


def photo_payload(photo: dict, orientation: str) -> dict:
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


def build_backgrounds() -> dict:
    pool = commons_background._all_pool()
    if not pool:
        print("warning: Commons pool unavailable; the demo will use the solid background")
        return {"landscape": [], "portrait": []}
    landscape = commons_background._candidates(pool, "landscape") or pool
    portrait = commons_background._candidates(pool, "portrait") or pool
    return {
        "landscape": [photo_payload(p, "landscape") for p in landscape[:BACKGROUND_POOL_SIZE]],
        "portrait": [photo_payload(p, "portrait") for p in portrait[:BACKGROUND_POOL_SIZE]],
    }


def build_snapshot() -> dict:
    client = app.test_client()

    statements = client.get(f"/api/statements?days={STATEMENT_DAYS}").get_json()
    vix = client.get("/api/vix?days=30").get_json()

    reactions: dict = {}
    for statement in statements:
        statement_id = statement.get("id")
        if not statement_id:
            continue
        response = client.get(f"/api/reactions/{quote(statement_id, safe='')}")
        if response.status_code == 200:
            reactions[statement_id] = response.get_json()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "app_version": app_version(),
        "snapshot_version": now.strftime("%d.%m.%y"),
        "data_as_of": now.strftime("%d %b %Y, %H:%M UTC"),
        "generated_at": now.isoformat(),
        "repo_url": REPO_URL,
        "statements": statements,
        "vix": vix,
        "reactions": reactions,
        "backgrounds": build_backgrounds(),
    }


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"error: expected exactly one {label} anchor, found {count}")
    return text.replace(old, new)


def write_demo(out: Path, snapshot: dict) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)

    shutil.copy2(ROOT / "static" / "logo.svg", out / "static" / "logo.svg")

    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    (out / "static" / "style.css").write_text(css.rstrip() + "\n" + BANNER_CSS, encoding="utf-8")

    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    html = replace_once(html, BANNER_ANCHOR, BANNER_INSERT, "banner")
    html = replace_once(html, SCRIPT_ANCHOR, SCRIPT_INSERT, "script")
    html = replace_once(html, PLAYVIDEO_ANCHOR, PLAYVIDEO_INSERT, "playVideo")
    (out / "index.html").write_text(html, encoding="utf-8")

    # `</` would otherwise close the surrounding <script> tag inside the JSON.
    data = json.dumps(snapshot, ensure_ascii=False, indent=2).replace("</", "<\\/")
    (out / "snapshot.js").write_text(f"window.__SNAPSHOT__ = {data};\n", encoding="utf-8")

    (out / "demo.js").write_text(DEMO_JS, encoding="utf-8")
    (out / "netlify.toml").write_text(NETLIFY_TOML, encoding="utf-8")
    (out / "README.md").write_text(
        README_TEMPLATE.format(
            app_version=snapshot["app_version"],
            snapshot_version=snapshot["snapshot_version"],
            data_as_of=snapshot["data_as_of"],
            generated_at=snapshot["generated_at"],
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist-demo", help="output directory (default: dist-demo)")
    args = parser.parse_args()

    out = (ROOT / args.out).resolve()
    snapshot = build_snapshot()
    write_demo(out, snapshot)

    print(f"Exported demo snapshot to {out}")
    print(f"  app version:      {snapshot['app_version']}")
    print(f"  snapshot version: {snapshot['snapshot_version']}")
    print(f"  data as of:       {snapshot['data_as_of']}")
    print(f"  statements:       {len(snapshot['statements'])}")
    print(f"  reactions:        {len(snapshot['reactions'])}")
    print(
        "  backgrounds:      "
        f"{len(snapshot['backgrounds']['landscape'])} landscape, "
        f"{len(snapshot['backgrounds']['portrait'])} portrait"
    )


if __name__ == "__main__":
    main()
