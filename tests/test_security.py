"""Security-focused tests for the local server and its outbound fetch guard.

Run with:  .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import media_cache  # noqa: E402
import net_guard  # noqa: E402
import server  # noqa: E402


# ── HTML sanitizer ──

def test_sanitizer_strips_attributes_from_allowed_tags():
    clean, _ = server.prepare_statement('<p onclick="alert(1)">hi</p>')
    assert "onclick" not in clean
    assert clean == "<p>hi</p>"


def test_sanitizer_strips_style_attribute():
    clean, _ = server.prepare_statement('<p style="position:fixed;inset:0">hi</p>')
    assert "style" not in clean


def test_sanitizer_keeps_safe_links_and_drops_javascript():
    clean, links = server.prepare_statement('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in clean
    assert links == ()

    clean, links = server.prepare_statement('<a href="https://ok.example">x</a>')
    assert 'href="https://ok.example"' in clean
    assert links == ("https://ok.example",)


def test_sanitizer_unwraps_unknown_tags():
    clean, _ = server.prepare_statement("<script>alert(1)</script><b>bold</b>")
    assert "script" not in clean
    assert "<b>" not in clean
    assert clean == "bold"


# ── SSRF guard ──

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://10.0.0.5/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/x",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://user:pass@example.com/x",
    "https://",
])
def test_check_url_blocks_dangerous_targets(url):
    # Literal IPs and bad schemes never reach DNS, so no patching is needed.
    with pytest.raises(net_guard.BlockedURL):
        net_guard.check_url(url)


def test_check_url_blocks_ipv4_mapped_ipv6():
    assert net_guard._is_blocked_ip("::ffff:127.0.0.1")


def test_check_url_allows_public_host(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["93.184.216.34"])
    net_guard.check_url("https://example.com/page")


def test_check_url_blocks_private_resolution(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["192.168.1.10"])
    with pytest.raises(net_guard.BlockedURL):
        net_guard.check_url("https://internal.example/")


def test_safe_get_revalidates_redirects(monkeypatch):
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)

        class Resp:
            status_code = 302
            headers = {"location": "http://internal.example/secret"}
            url = "http://public.example/start"

            def close(self):
                pass

        return Resp()

    monkeypatch.setattr(
        net_guard, "_resolve",
        lambda host: ["93.184.216.34"] if host == "public.example" else ["10.0.0.9"],
    )
    monkeypatch.setattr(net_guard.requests, "get", fake_get)

    with pytest.raises(net_guard.BlockedURL):
        net_guard.safe_get("http://public.example/start")
    assert calls == ["http://public.example/start"]


def test_safe_get_follows_public_redirect(monkeypatch):
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)

        class Resp:
            def __init__(self, status_code, headers):
                self.status_code = status_code
                self.headers = headers
                self.url = url

            def close(self):
                pass

        if url == "http://public.example/start":
            return Resp(302, {"location": "http://cdn.example/final"})
        return Resp(200, {})

    monkeypatch.setattr(net_guard, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(net_guard.requests, "get", fake_get)

    response = net_guard.safe_get("http://public.example/start")
    assert response.status_code == 200
    assert calls == ["http://public.example/start", "http://cdn.example/final"]


# ── Refresh gating ──

@pytest.fixture(autouse=True)
def _refresh_defaults(monkeypatch):
    monkeypatch.setattr(server, "_last_http_refresh", 0.0)
    monkeypatch.setattr(server, "REFRESH_MIN_INTERVAL", 0)
    monkeypatch.setattr(server, "REFRESH_ALLOW_REMOTE", False)
    yield


@pytest.fixture
def client():
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def test_refresh_rejects_cross_origin(client, monkeypatch):
    monkeypatch.setattr(server, "start_refresh", lambda: pytest.fail("refresh must not start"))
    resp = client.post("/api/refresh", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


def test_refresh_rejects_cross_site_fetch_metadata(client, monkeypatch):
    monkeypatch.setattr(server, "start_refresh", lambda: pytest.fail("refresh must not start"))
    resp = client.post("/api/refresh", headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403


def test_refresh_blocked_for_remote_host(client, monkeypatch):
    monkeypatch.setattr(server, "start_refresh", lambda: pytest.fail("refresh must not start"))
    resp = client.post("/api/refresh", environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
    assert resp.status_code == 403


def test_refresh_allowed_from_localhost(client, monkeypatch):
    started = {"called": False}

    def fake_start():
        started["called"] = True
        return True

    monkeypatch.setattr(server, "start_refresh", fake_start)
    resp = client.post("/api/refresh")
    assert resp.status_code == 202
    assert started["called"]


def test_refresh_rate_limited(client, monkeypatch):
    monkeypatch.setattr(server, "REFRESH_MIN_INTERVAL", 300)
    monkeypatch.setattr(server, "_last_http_refresh", time.monotonic())
    monkeypatch.setattr(server, "start_refresh", lambda: pytest.fail("refresh must not start"))
    resp = client.post("/api/refresh")
    assert resp.status_code == 429
    assert resp.get_json()["retry_after_s"] > 0


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_refresh_status_can_refresh_locally(client):
    assert client.get("/api/refresh/status").get_json()["can_refresh"] is True


def test_refresh_status_hides_output_for_remote(client, monkeypatch):
    monkeypatch.setattr(server, "_refresh_status", {
        "running": False,
        "started_at": None,
        "current_step": None,
        "last_run": None,
        "results": [{
            "step": "rss", "status": "ok", "duration_s": 1.0,
            "stdout": "/home/secret/path", "stderr": "boom",
        }],
    })
    resp = client.get("/api/refresh/status", environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
    assert resp.get_json()["results"][0] == {"step": "rss", "status": "ok", "duration_s": 1.0}


# ── Video cache cap ──

def test_video_cache_cap_evicts_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(media_cache, "_cache_dir", str(tmp_path))
    monkeypatch.setattr(media_cache, "MAX_CACHE_BYTES", 100)

    old = tmp_path / "old.mp4"
    old.write_bytes(b"x" * 80)
    new = tmp_path / "new.mp4"
    new.write_bytes(b"y" * 80)
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    media_cache._enforce_cache_cap()

    assert not old.exists()
    assert new.exists()
