#!/usr/bin/env bash
#
# Cut a new static demo snapshot from the latest local data and publish it to
# the trumpvix-demo repository. See .opencode/skills/demo-snapshot/SKILL.md.
#
# Usage:
#   scripts/update_demo.sh
#
# Environment:
#   REFRESH=0        skip the data ingestion step (default: 1, refresh first)
#   PUSH=0           build and commit locally, but don't push (default: 1)
#   RELEASE=0        skip creating a GitHub release (default: 1)
#   APP_DIR=<path>   app repo (default: /home/jonpinto/trumpvix)
#   DEMO_DIR=<path>  demo working copy (default: /home/jonpinto/trumpvix-demo)
set -euo pipefail

APP_DIR="${APP_DIR:-/home/jonpinto/trumpvix}"
DEMO_DIR="${DEMO_DIR:-/home/jonpinto/trumpvix-demo}"
REFRESH="${REFRESH:-1}"
PUSH="${PUSH:-1}"
RELEASE="${RELEASE:-1}"

PY="$APP_DIR/.venv/bin/python"

say() { printf '\n=== %s ===\n' "$1"; }

# ── Preflight ──
say "Preflight"
[ -d "$APP_DIR/.git" ] || { echo "error: app repo not found at $APP_DIR" >&2; exit 1; }
[ -x "$PY" ] || { echo "error: venv python not found at $PY" >&2; exit 1; }
[ -f "$APP_DIR/scripts/export_demo.py" ] || { echo "error: scripts/export_demo.py missing" >&2; exit 1; }
[ -d "$DEMO_DIR/.git" ] || { echo "error: demo working copy not found at $DEMO_DIR" >&2; exit 1; }

cd "$APP_DIR"
if [ -n "$(git status --porcelain)" ]; then
  echo "warning: app repo has uncommitted changes; the snapshot reflects the data, not the working tree"
fi

# ── Refresh data (optional) ──
if [ "$REFRESH" = "1" ]; then
  say "Refreshing data (scripts/deploy.sh)"
  # deploy.sh calls `python3`; activate the venv so it resolves to the venv python.
  # shellcheck disable=SC1091
  source "$APP_DIR/.venv/bin/activate"
  bash "$APP_DIR/scripts/deploy.sh"
  deactivate
else
  echo "Skipping data refresh (REFRESH=0)"
fi

# ── Export ──
say "Exporting snapshot"
cd "$APP_DIR"
"$PY" scripts/export_demo.py

SNAP_VERSION=$(grep -m1 '"snapshot_version"' dist-demo/snapshot.js | sed -E 's/.*"snapshot_version": "([^"]+)".*/\1/')
APP_VERSION=$(grep -m1 '"app_version"' dist-demo/snapshot.js | sed -E 's/.*"app_version": "([^"]+)".*/\1/')
DATA_AS_OF=$(grep -m1 '"data_as_of"' dist-demo/snapshot.js | sed -E 's/.*"data_as_of": "([^"]+)".*/\1/')
TAG="snap-$SNAP_VERSION"
echo "app $APP_VERSION · snapshot $SNAP_VERSION · data as of $DATA_AS_OF"

# ── Validate ──
say "Validating"
if command -v node >/dev/null 2>&1; then
  node --check dist-demo/demo.js
  node --check dist-demo/snapshot.js
  echo "javascript: syntax OK"
else
  echo "node not found; skipping JS syntax check"
fi

# ── Publish ──
say "Publishing to $DEMO_DIR"
cd "$DEMO_DIR"
git pull --ff-only

cp "$APP_DIR/dist-demo/index.html" index.html
cp "$APP_DIR/dist-demo/demo.js" demo.js
cp "$APP_DIR/dist-demo/snapshot.js" snapshot.js
cp "$APP_DIR/dist-demo/README.md" README.md
cp "$APP_DIR/dist-demo/netlify.toml" netlify.toml
cp "$APP_DIR/dist-demo/static/style.css" static/style.css
cp "$APP_DIR/dist-demo/static/logo.svg" static/logo.svg

git add -A
if git diff --cached --quiet; then
  echo "no file changes (snapshot already current)"
else
  git -c user.name="${GIT_AUTHOR_NAME:-JonPinto}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-JonPinto@users.noreply.github.com}" \
      commit -m "Snapshot $SNAP_VERSION (app $APP_VERSION)"
fi

# Move the snapshot tag to this commit (re-running the same day updates it).
git tag -f "$TAG" -m "Snapshot $SNAP_VERSION — app $APP_VERSION, data as of $DATA_AS_OF"

if [ "$PUSH" = "1" ]; then
  git push origin HEAD
  git push --force origin "refs/tags/$TAG"
else
  echo "PUSH=0: skipped push"
fi

# ── GitHub release (best effort) ──
if [ "$RELEASE" = "1" ] && [ "$PUSH" = "1" ]; then
  say "GitHub release"
  TOKEN="${GITHUB_TOKEN:-}"
  if [ -z "$TOKEN" ] && [ -f "$HOME/.config/gh/hosts.yml" ]; then
    TOKEN=$(grep -m1 'oauth_token:' "$HOME/.config/gh/hosts.yml" | awk '{print $2}')
  fi
  SLUG=$(git remote get-url origin | sed -E 's#.*github.com[:/]([^/]+/[^/.]+)(\.git)?$#\1#')
  if [ -n "$TOKEN" ]; then
    "$PY" - "$SLUG" "$TOKEN" "$TAG" "$SNAP_VERSION" "$APP_VERSION" "$DATA_AS_OF" <<'PY'
import json, sys, urllib.error, urllib.request

slug, token, tag, snap, app, asof = sys.argv[1:7]
body = (
    f"Static demo snapshot **{snap}** of "
    f"[TrumpVIX](https://github.com/JonPinto/trumpvix) (app `{app}`). "
    f"Data up to date as of {asof}."
)
payload = json.dumps({
    "tag_name": tag,
    "name": f"Snapshot {snap} — app {app}",
    "body": body,
}).encode()
request = urllib.request.Request(
    f"https://api.github.com/repos/{slug}/releases",
    data=payload,
    method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(request) as response:
        print("release created:", response.status)
except urllib.error.HTTPError as error:
    if error.code == 422:
        print("release already exists")
    else:
        print("warning: release returned", error.code, error.read().decode()[:200])
PY
  else
    echo "no GitHub token found; skipping release (tag still pushed)"
  fi
fi

# ── Report ──
say "Done"
echo "app version:      $APP_VERSION"
echo "snapshot version: $SNAP_VERSION"
echo "data as of:       $DATA_AS_OF"
echo "demo tag:         $TAG"
echo "demo repo:        https://github.com/JonPinto/trumpvix-demo"
echo "Netlify redeploys automatically on push."
