"""Hardened HTTP layer shared by all adapters.

Security properties:

* SSRF guard — private/loopback/link-local hosts are refused unless explicitly
  allowed (``RADAR_ALLOW_PRIVATE=1``, used only by the local test-suite).
* Content-type allowlist — only HTML/XML/JSON-ish payloads are accepted, so a
  hostile URL cannot feed us gigabytes of binary junk.
* Hard size cap — responses are truncated at ``MAX_BYTES``.
* Politeness — per-request timeout plus a global rate limiter.
* Fetched content is treated as *data*, never executed or rendered.

Only the Python standard library is used (urllib) to keep the dependency
surface minimal and auditable.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "ai-research-radar/0.1 (+personal research tool; "
    "https://github.com/xyyyang97/ai-research-radar)"
)

MAX_BYTES = 2_000_000  # 2 MB hard cap per response
DEFAULT_TIMEOUT = 20.0

_ALLOWED_CONTENT_PREFIXES = (
    "text/html", "text/plain", "application/xhtml", "application/xml",
    "text/xml", "application/rss", "application/atom", "application/json",
    "application/feed",
)

_last_request_at = 0.0


def _allow_private() -> bool:
    return os.environ.get("RADAR_ALLOW_PRIVATE", "") not in ("", "0", "false")


# RFC 2544 benchmarking range. Never routed on real intranets, but commonly
# returned by transparent proxies / VPN clients in "fake-IP" DNS mode
# (Clash, sing-box, ...). Blocking it would break every such setup while the
# genuinely dangerous ranges below stay blocked.
_FAKEIP_NETS = [ipaddress.ip_network("198.18.0.0/15")]


def _assert_public_host(hostname: str) -> None:
    if _allow_private():
        return
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise RuntimeError(f"DNS resolution failed for {hostname!r}: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if any(addr in net_ for net_ in _FAKEIP_NETS):
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            raise RuntimeError(
                f"refusing to fetch private address {addr} for host {hostname!r} "
                "(set RADAR_ALLOW_PRIVATE=1 only for local testing)"
            )


class HttpResponse:
    __slots__ = ("body", "headers", "status")

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ctype)
        if m:
            charset = m.group(1)
        return self.body.decode(charset, errors="replace")


def _rate_limit(min_interval: float = 1.0) -> None:
    global _last_request_at
    wait = min_interval - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """GET *url* with SSRF guard, size cap, content-type check."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are supported: {url!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL without host: {url!r}")
    _assert_public_host(hostname)
    _rate_limit()

    merged_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    merged_headers.update(headers or {})

    request = urllib.request.Request(url, headers=merged_headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status = resp.status
            resp_headers = dict(resp.headers.items())
            content_type = resp_headers.get("Content-Type", "").lower()
            if content_type and not any(
                content_type.startswith(p) for p in _ALLOWED_CONTENT_PREFIXES
            ):
                raise ValueError(f"unacceptable content-type {content_type!r} at {url}")
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RuntimeError(f"network error fetching {url}: {exc}") from exc

    if len(body) > MAX_BYTES:
        body = body[:MAX_BYTES]
    return HttpResponse(status=status, headers=resp_headers, body=body)


def fetch_json(url: str, *, token: str = "", timeout: float = DEFAULT_TIMEOUT,
               headers: dict[str, str] | None = None) -> Any_:
    """GET and JSON-decode with optional bearer auth (GitHub API)."""
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    resp = fetch(url, timeout=timeout, headers=h)
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}") from exc


Any_ = object  # minimal forward annotation target for fetch_json return


def post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict:
    """POST *payload* as JSON and decode the JSON response (used by LLM providers).

    Same SSRF guard / rate limiting as ``fetch``. Response cap raised to 8 MB
    because model completions can be verbose.
    """
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or ""
    _assert_public_host(hostname)
    _rate_limit(min_interval=0.2)

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT,
                 **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read(8_000_000)
            resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} posting {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RuntimeError(f"network error posting {url}: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response from {url}") from exc
