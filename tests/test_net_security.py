"""Security tests for the hardened network layer."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from ai_research_radar import net


class TestSSRFGuard:
    @pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "192.168.0.5",
                                      "172.16.9.9", "169.254.169.254"])
    def test_private_addrs_refused(self, addr):
        infos = [(socket.AF_INET, None, None, "", (addr, 0))]
        with (
            patch.object(socket, "getaddrinfo", return_value=infos),
            pytest.raises(RuntimeError, match="private address"),
        ):
            net._assert_public_host("evil.example.com")

    def test_metadata_endpoint_refused(self):
        infos = [(socket.AF_INET, None, None, "", ("169.254.169.254", 0))]
        with patch.object(
            socket, "getaddrinfo", return_value=infos,
        ), pytest.raises(RuntimeError):
            net._assert_public_host("metadata.google.internal")

    def test_fakeip_range_allowed_for_proxy_dns(self):
        infos = [(socket.AF_INET, None, None, "", ("198.18.0.21", 0))]
        with patch.object(socket, "getaddrinfo", return_value=infos):
            net._assert_public_host("api.github.com")  # must not raise

    def test_public_addr_allowed(self):
        infos = [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
        with patch.object(socket, "getaddrinfo", return_value=infos):
            net._assert_public_host("example.com")

    def test_allow_private_flag_bypasses(self, monkeypatch):
        monkeypatch.setenv("RADAR_ALLOW_PRIVATE", "1")
        infos = [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))]
        with patch.object(socket, "getaddrinfo", return_value=infos):
            net._assert_public_host("localhost")  # no raise


class TestFetchGuards:
    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValueError):
            net.fetch("ftp://example.com/file")

    def test_content_type_blocklist(self):
        class FakeResp:
            status = 200

            def __init__(self) -> None:
                self.headers = {"Content-Type": "application/octet-stream"}

            def read(self, n=-1):
                return b"\x00\x01binary"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            patch.object(net.socket, "getaddrinfo", return_value=[
                (socket.AF_INET, None, None, "", ("93.184.216.34", 0))]),
            patch.object(net.urllib.request, "urlopen", return_value=FakeResp()),
            pytest.raises(ValueError, match="content-type"),
        ):
            net.fetch("https://example.com/bad")


class TestUntrustedContent:
    def test_billion_laughs_feed_never_parsed(self, tmp_path):
        """A DTD-bearing XML payload must be rejected before parsing."""
        from ai_research_radar.adapters.rss import _DTD_RE, FeedAdapter

        evil = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            "<rss><channel><item><title>&lol2;</title></item></channel></rss>"
        )
        assert _DTD_RE.search(evil[:2048])
        adapter = FeedAdapter(url="https://attacker.example/feed",
                              source_type="rss", config=None)

        class FakeResp:
            text = evil

        from ai_research_radar.adapters import rss as rss_mod

        orig = rss_mod.net.fetch
        rss_mod.net.fetch = lambda url, **kw: FakeResp()
        try:
            result = adapter.fetch()
        finally:
            rss_mod.net.fetch = orig
        assert result.items == []
        assert result.errors and "DOCTYPE" in result.errors[0]
