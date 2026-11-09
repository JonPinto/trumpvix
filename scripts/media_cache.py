"""Per-run disk cache for videos proxied to the browser.

Videos are downloaded once per server run, into a temp directory that is removed when the
server exits. Directories left behind by a crashed run are swept on the next start.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
import threading

import requests
from flask import Response, jsonify, send_file

MAX_VIDEO_BYTES = 500 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
DIR_PREFIX = "trumpvix-media-"

_cache_dir: str | None = None
_cache_dir_lock = threading.Lock()
_url_locks: dict[str, threading.Lock] = {}
_url_locks_lock = threading.Lock()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep_stale_dirs() -> None:
    """Remove cache dirs from previous runs whose process is gone."""
    root = tempfile.gettempdir()
    for name in os.listdir(root):
        if not name.startswith(DIR_PREFIX):
            continue
        pid_part = name[len(DIR_PREFIX):].split("-", 1)[0]
        if pid_part.isdigit() and not _pid_alive(int(pid_part)):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)


def cache_dir() -> str:
    global _cache_dir
    with _cache_dir_lock:
        if _cache_dir is None:
            _sweep_stale_dirs()
            _cache_dir = tempfile.mkdtemp(prefix=f"{DIR_PREFIX}{os.getpid()}-")
            atexit.register(shutil.rmtree, _cache_dir, True)
        return _cache_dir


def _lock_for(url: str) -> threading.Lock:
    with _url_locks_lock:
        return _url_locks.setdefault(url, threading.Lock())


def video_response(url: str) -> Response:
    """Serve a video from the cache, or stream it from upstream while caching it."""
    path = os.path.join(cache_dir(), hashlib.sha256(url.encode()).hexdigest() + ".mp4")
    if os.path.exists(path):
        return send_file(path, mimetype="video/mp4", conditional=True, max_age=0)

    try:
        upstream = requests.get(url, stream=True, timeout=(10, 60))
        upstream.raise_for_status()
    except requests.RequestException as err:
        return jsonify({"error": f"couldn't fetch video: {err}"}), 502

    length = int(upstream.headers.get("Content-Length") or 0)
    if length > MAX_VIDEO_BYTES:
        upstream.close()
        return jsonify({"error": "video too large", "bytes": length}), 413

    # Only one request writes a given video to the cache; a concurrent request for the
    # same video is streamed straight through without caching.
    lock = _lock_for(url)
    caching = lock.acquire(blocking=False)
    part_path = f"{path}.{threading.get_ident()}.part"
    out = open(part_path, "wb") if caching else None
    state = {"bytes": 0, "complete": False}

    def generate():
        for chunk in upstream.iter_content(CHUNK_BYTES):
            if out:
                out.write(chunk)
            state["bytes"] += len(chunk)
            yield chunk
        state["complete"] = True

    def cleanup():
        # Runs when the response is closed: after the last chunk or on client disconnect.
        upstream.close()
        if out is None:
            return
        out.close()
        try:
            if state["complete"] and (not length or state["bytes"] == length):
                os.replace(part_path, path)
            else:
                os.remove(part_path)
        except OSError:
            pass
        finally:
            lock.release()

    headers = {"Content-Length": str(length)} if length else {}
    response = Response(generate(), mimetype="video/mp4", headers=headers)
    response.call_on_close(cleanup)
    return response
