---
name: Update Demo Snapshot
description: Cut a new static demo snapshot from the latest local TrumpVIX data and publish it to the trumpvix-demo GitHub repo — regenerates the baked data, top banner and README, and tags the demo repo with the new snapshot version.
---

# Update Demo Snapshot

Publishes a fresh read-only snapshot of the dashboard to the demo repository
(`github.com/JonPinto/trumpvix-demo`), which Netlify serves as a static site.

## When to use

Use this when the user wants to refresh the public demo with current data, cut a
new demo version, or update the demo's top banner and README. Do **not** use it
to change the demo's look or behaviour: that is produced by
`scripts/export_demo.py`, and the change should be made there (the demo is
generated output, not hand-edited).

## How it works

The demo is generated, not hand-written:

- `scripts/export_demo.py` (app repo) bakes the latest data into `dist-demo/`:
  `snapshot.js` (the data), the top banner text (app version + snapshot date),
  the demo `README.md`, and the live-background shim.
- `scripts/update_demo.sh` (app repo) runs the export, validates it, copies it
  into the demo working copy, then commits, tags and pushes.

Versioning, all derived automatically:

| Thing | Value |
|---|---|
| App version | latest git tag of the app repo (e.g. `v1.2`) |
| Snapshot version | `dd.mm.yy`, UTC |
| Demo git tag | `snap-<dd.mm.yy>` |

## Run it

```bash
/home/jonpinto/trumpvix/scripts/update_demo.sh
```

Defaults: refresh data first, validate, then push and create a GitHub release.
Override with environment variables:

| Variable | Default | Effect |
|---|---|---|
| `REFRESH=0` | `1` | Skip ingestion; re-bake whatever is already in the DB |
| `PUSH=0` | `1` | Build and commit locally, but do not push |
| `RELEASE=0` | `1` | Do not create a GitHub release |
| `APP_DIR` | `/home/jonpinto/trumpvix` | App repo location |
| `DEMO_DIR` | `/home/jonpinto/trumpvix-demo` | Demo working copy |

## Guardrails

- Both repos push over SSH (`git@github.com:...`); the stored HTTPS token cannot
  push (missing workflow scope).
- `dist-demo/` is gitignored in the app repo — never commit it.
- If validation fails, fix the cause; do not push a broken snapshot.
- Re-running on the same day moves the `snap-<dd.mm.yy>` tag to the newest
  commit and reports that the release already exists.
- After a push, Netlify redeploys automatically; no extra step is needed.

## Verify and report

- `node --check dist-demo/demo.js` and `dist-demo/snapshot.js` pass.
- Optionally serve `dist-demo/` (`python -m http.server`) and check the banner,
  the data, and a live Commons background.
- Report the app version, snapshot version, the data-as-of line, the commit and
  the tag, plus the demo repo link.
