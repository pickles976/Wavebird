from datetime import UTC, datetime, timedelta

import httpx
import pytest

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.directory import (
    DIRECTORY_KEY,
    TickerDirectoryClient,
    parse_ticker_directory,
)
from wavebird.errors import ProviderSchemaError, TransportError

NOW = datetime(2026, 1, 1, tzinfo=UTC)

SEC_PAYLOAD = {
    "1": {"cik_str": 789019, "ticker": "msft", "title": "MICROSOFT CORP"},
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
}


def make_client(cache, handler):
    return TickerDirectoryClient(
        cache=cache,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )


def test_parse_ticker_directory_normalizes_and_sorts() -> None:
    records = parse_ticker_directory(SEC_PAYLOAD)
    assert [record["ticker"] for record in records] == ["AAPL", "GOOG", "MSFT"]
    assert records[0]["title"] == "Apple Inc."
    assert records[0]["cik"] == 320193


def test_parse_rejects_unexpected_shape() -> None:
    with pytest.raises(ProviderSchemaError):
        parse_ticker_directory({"unexpected": True})


def test_fresh_directory_prevents_http(tmp_path) -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put(
        DIRECTORY_KEY,
        {"tickers": [["AAPL", "Apple Inc.", 320193]]},
        NOW,
        NOW + timedelta(days=30),
        "https://www.sec.gov/files/company_tickers.json",
    )
    client = make_client(cache, handler)
    assert client.load() == ("AAPL",)
    assert calls == 0
    cache.close()


def test_miss_fetches_and_caches(tmp_path) -> None:
    requests_seen = []

    def handler(request):
        requests_seen.append(request)
        return httpx.Response(200, json=SEC_PAYLOAD)

    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    client = make_client(cache, handler)
    assert client.load() == ("AAPL", "GOOG", "MSFT")
    assert requests_seen[0].headers["User-Agent"].startswith("Wavebird/")
    assert client.load() == ("AAPL", "GOOG", "MSFT")
    assert len(requests_seen) == 1
    assert cache.get(DIRECTORY_KEY) is not None
    cache.close()


def test_stale_directory_used_on_transport_failure(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put(
        DIRECTORY_KEY,
        {"tickers": [["OLD", "Old Co", 1]]},
        NOW - timedelta(days=60),
        NOW - timedelta(days=30),
        "https://www.sec.gov/files/company_tickers.json",
    )

    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    client = make_client(cache, handler)
    assert client.load() == ("OLD",)
    cache.close()


def test_invalid_json_is_schema_error(tmp_path) -> None:
    client = make_client(
        SQLiteCache(tmp_path / "cache.sqlite3"),
        lambda request: httpx.Response(200, content=b"not json"),
    )
    with pytest.raises(ProviderSchemaError):
        client.load()


def test_http_error_status_is_transport_error(tmp_path) -> None:
    client = make_client(
        SQLiteCache(tmp_path / "cache.sqlite3"),
        lambda request: httpx.Response(403),
    )
    with pytest.raises(TransportError):
        client.load()


def test_ticker_suggestions_merge_sources(tmp_path) -> None:
    from test_interactive import FakeUI

    from wavebird.interactive import ticker_suggestions

    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put(
        CacheKey("alpha_vantage", "EARNINGS", "ZM", "EARNINGS:ZM"),
        {},
        NOW,
        NOW + timedelta(days=1),
        "fake://",
    )
    ui = FakeUI(prompts=[], radiolist_results=None)
    suggestions = ticker_suggestions(cache, None, ui)
    assert "ZM" in suggestions
    assert "AAPL" in suggestions
    assert "IBM" in suggestions
    assert not ui.notifications
    cache.close()
