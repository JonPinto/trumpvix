"""Guard outbound fetches whose URLs come from third-party content.

Link previews, status-page scraping and the video proxy all fetch addresses that
were written in the RSS feed, so the server must not be tricked into fetching
something on the local network or the loopback interface (SSRF).

`safe_get` validates every hop, including redirects, because a public URL can
redirect to an internal one. It checks the addresses a host resolves to, not just
the URL string, so DNS names pointing at private ranges are refused too.

Known limitation: the name is resolved for validation and then again by the HTTP
client, so a DNS-rebinding attacker could in principle change the answer in
between. That is acceptable for a local dashboard; a stricter deployment would
connect to the validated address directly.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 3
REDIRECT_CODES = (301, 302, 303, 307, 308)


class BlockedURL(ValueError):
    """Raised when a URL points somewhere the server must not fetch."""


def _is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    # An IPv6 address can wrap an IPv4 one (e.g. ::ffff:127.0.0.1).
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped is not None and _is_blocked_ip(str(mapped))


def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def check_url(url: str) -> None:
    """Raise BlockedURL unless url is http(s) and resolves only to public addresses."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise BlockedURL(f"scheme not allowed: {parts.scheme or '(none)'}")
    if not parts.hostname:
        raise BlockedURL("no host in URL")
    if parts.username or parts.password:
        raise BlockedURL("credentials in URL")

    try:
        addresses = _resolve(parts.hostname)
    except socket.gaierror as err:
        raise BlockedURL(f"cannot resolve host: {err}") from err
    if not addresses:
        raise BlockedURL("host did not resolve")

    for ip in addresses:
        if _is_blocked_ip(ip):
            raise BlockedURL(f"host resolves to a non-public address: {ip}")


def safe_get(url: str, *, max_redirects: int = MAX_REDIRECTS, **kwargs) -> requests.Response:
    """GET a URL from untrusted content, refusing internal targets at every hop.

    Redirects are followed manually so each destination is validated, closing the
    redirect-based SSRF bypass. Returns the response; the caller must close it.
    """
    kwargs.pop("allow_redirects", None)
    current = url
    for _ in range(max_redirects + 1):
        check_url(current)
        response = requests.get(current, allow_redirects=False, **kwargs)
        location = response.headers.get("location")
        if response.status_code in REDIRECT_CODES and location:
            response.close()
            current = urljoin(current, location)
            continue
        return response
    raise BlockedURL("too many redirects")
