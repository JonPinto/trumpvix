# Changelog

All notable changes to TrumpVIX are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `scripts/export_demo.py`, which builds a read-only static snapshot of the
  dashboard (frontend plus baked data) into `dist-demo/` for the
  [demo site](https://github.com/JonPinto/trumpvix-demo).
- `scripts/update_demo.sh`, which exports the snapshot and publishes and tags
  it in the demo repository, plus a `demo-snapshot` OpenCode skill wrapping it.

### Changed

- Rewrote the README for the current v1.2 state: quick start, first-run
  behaviour, configuration, project layout, API notes, service and security
  guidance, troubleshooting, and refreshed limitations.
- Added a desktop screenshot to the README.

### Removed

- Render deployment artifacts (`render.yaml`, `Procfile`, keep-alive and
  deploy-triggered ingestion workflows). The app needs a persistent disk and
  an always-on process, which Render's free tier does not provide.

### Fixed

- The header VIX no longer lags a day behind. It now ingests the `^VIX` index
  intraday and extends FRED's daily closes with the newest intraday value, so
  the chart is current even when FRED has not published the latest close. The
  old `VXX` proxy (a VIX-futures ETN) is no longer used.
- `scripts/deploy.sh` uses `python3`, matching the README and working on
  systems without a `python` alias.

## [v1.2] - 2026-11-09

Media, backgrounds and mobile.

### Added

- **Media grid and full-screen viewer.** Posts with several images or videos
  render as a 1–4 tile grid; clicking a tile opens it full-size inside the
  card, and ‹ › cycle through every item. ✕ or Esc returns to the grid.
- **Video playback.** Videos show a poster with a centred play button. Opening
  one from the grid starts it; cycling to one waits for play. Downloads run
  through the server with a progress bar, then play in place.
- **Per-run video cache.** `/api/video` caches downloaded videos in a temp
  folder for the rest of the server run (500 MB cap), removing them on exit
  and cleaning up after crashed runs.
- **Wikimedia Commons backgrounds.** Each page load or data refresh picks a
  random Trump-related Commons photo: landscape on desktop, portrait on
  phones. The photo fades in over the solid colour, with photographer and
  licence credited at the bottom-left on wide screens.
- **Live refresh feedback.** The refresh button shows running/failed states,
  the page reloads in place as each data source lands, and a refresh already
  running when the page opens is followed automatically.
- **Mobile layout.** Phones get a larger type scale and tap targets, a
  two-row search bar, a stacked card header, and a timeline with only the
  direction buttons.

### Changed

- **Much faster refresh.** A full refresh drops from ~95 s to ~6 s: feed media
  replaces per-post page scraping, yfinance bars are fetched in one batched
  request, and FRED/link-preview fetches run concurrently.
- **Leaner server.** Reaction lookups use indexed single-row queries
  (~213 ms → ~1.2 ms) and statement rendering is cached (~17.7 ms → ~1.1 ms).
- **Single source of truth** for market series (`scripts/series.py`), with the
  schema defined only in `db.py`; `/api/refresh` is POST-only.
- **Dependencies:** requests 2.34.2, Flask 3.1.3, feedparser 6.0.14,
  yfinance 1.8.0; Python 3.14 is the tested runtime.

### Fixed

- VIX always uses FRED's VIXCLS series; the VXX fallback is labelled as a
  proxy and honours the requested window.
- Reaction queries prefer the FRED indicator for each series, and statement
  ids containing slashes work.
- Statement HTML keeps only http(s)/mailto links; frontend attributes and
  link-preview titles are escaped.
- The date picker filters by date instead of matching the date as text; the
  card summary is labelled S&P 500 and handles partial/daily/awaiting-open
  states.
- Link previews check content type and cap HTML at 2 MB, so video/image links
  no longer hang the fetch; redirects map back to the original URL.
- Refresh no longer crashes at the end of a run, subprocesses use the running
  interpreter, and the running flag is set before the thread starts.
- RSS fallback IDs use a stable sha256 hash instead of `hash()`.

### Documentation

- README refreshed for media, backgrounds, mobile layout, APIs, scripts and
  known issues; the interactive VIX roadmap item moves to v1.3.
- Added this changelog.

## [v1.1] - 2026-06-06

Render deployment, market-data fixes and mobile support.

### Added

- Render deployment (root `app.py` for gunicorn, `Procfile`, `render.yaml`)
  and deploy-triggered ingestion.
- Non-blocking `/api/refresh` with background execution and status.
- Reactions render below the card on small screens.

### Fixed

- Intraday ingestion registers its series as indicators, and VIX falls back to
  VXX when FRED is unavailable.
- Missing timezone import in intraday ingestion.
- `PYTHONPATH` for refresh subprocesses, and the DB schema is created on
  connect.

## [v1.0] - 2026-06-04

Initial local MVP.

- Ingestion pipeline for Truth Social posts, FRED indicators, intraday prices
  and OpenGraph link previews.
- 12 market series, 10-hour market-time reaction model, card summaries and
  side-by-side reaction panels.
- Swipe/keyboard navigation, floating timeline, keyword search, date picker
  and loading animations.

[Unreleased]: https://github.com/JonPinto/trumpvix/compare/v1.2...HEAD
[v1.2]: https://github.com/JonPinto/trumpvix/compare/v1.1...v1.2
[v1.1]: https://github.com/JonPinto/trumpvix/compare/v1.0...v1.1
[v1.0]: https://github.com/JonPinto/trumpvix/releases/tag/v1.0
