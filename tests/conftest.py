"""Shared pytest fixtures for the whole test suite."""

from __future__ import annotations

import pytest

# Every test that touches the SWING_20 fetch/prepare pipeline is expected to
# monkeypatch get_stock_data -- no test should ever reach the real network.
# But yfinance's HTTP layer mixes stdlib-socket-based `requests` with
# native-libcurl-based `curl_cffi` (used for anti-bot impersonation), and
# `curl_cffi` bypasses Python's `socket` module entirely, so a socket-level
# monkeypatch cannot reliably intercept it. Redirecting all outbound HTTP(S)
# to a dead local port works underneath both clients: any accidentally
# un-mocked real call fails in seconds (connection refused) instead of
# hanging for minutes waiting on a slow or rate-limiting remote server.
_DEAD_PROXY = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def block_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, _DEAD_PROXY)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "")
