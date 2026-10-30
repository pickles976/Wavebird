import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wavebird.cache import CacheKey, SQLiteCache


def test_cache_miss_hit_expiry_and_atomic_replace(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    key = CacheKey("alpha_vantage", "BALANCE_SHEET", "IBM", "BALANCE_SHEET:IBM")

    assert cache.get(key) is None
    cache.put(key, {"version": 1}, now, now + timedelta(days=7), "https://example.test")
    first = cache.get(key)
    assert first is not None and first.payload == {"version": 1}
    assert first.is_fresh(now + timedelta(days=6))
    assert not first.is_fresh(now + timedelta(days=8))

    cache.put(key, {"version": 2}, now, now + timedelta(days=7), "https://example.test")
    replaced = cache.get(key)
    assert replaced is not None and replaced.payload == {"version": 2}
    cache.close()


def test_symbols_are_distinct(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    ibm = CacheKey("alpha_vantage", "CASH_FLOW", "IBM", "CASH_FLOW:IBM")
    aapl = CacheKey("alpha_vantage", "CASH_FLOW", "AAPL", "CASH_FLOW:AAPL")
    cache.put(ibm, {"symbol": "IBM"}, now, now + timedelta(days=7), "https://example.test")
    cache.put(aapl, {"symbol": "AAPL"}, now, now + timedelta(days=7), "https://example.test")
    ibm_record = cache.get(ibm)
    aapl_record = cache.get(aapl)
    assert ibm_record is not None and ibm_record.payload == {"symbol": "IBM"}
    assert aapl_record is not None and aapl_record.payload == {"symbol": "AAPL"}
    cache.close()


def test_request_counter_tracks_provider_per_day(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    assert cache.requests_today("alpha_vantage", now) == 0
    for _ in range(3):
        cache.record_request("alpha_vantage", now)
    cache.record_request("damodaran", now)
    assert cache.requests_today("alpha_vantage", now) == 3
    assert cache.requests_today("damodaran", now) == 1
    next_day = now + timedelta(days=1)
    assert cache.requests_today("alpha_vantage", next_day) == 0
    cache.close()


def test_put_is_atomic_for_other_connections(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = tmp_path / "cache.sqlite3"
    cache = SQLiteCache(path)
    key = CacheKey("alpha_vantage", "EARNINGS", "IBM", "EARNINGS:IBM")
    cache.put(
        key,
        {"annualEarnings": [{"fiscalDateEnding": "2025-12-31", "reportedEPS": "9.53"}]},
        now,
        now + timedelta(days=7),
        "https://example.test",
    )
    cache.close()

    outside = sqlite3.connect(path)
    try:
        row = outside.execute(
            "SELECT payload_json, source_url FROM cache_entries "
            "WHERE provider = ? AND dataset = ? AND symbol = ? AND request_key = ?",
            (key.provider, key.dataset, key.symbol, key.request_key),
        ).fetchone()
    finally:
        outside.close()
    assert row is not None
    payload_json, source_url = row
    assert "reportedEPS" in payload_json
    assert source_url == "https://example.test"
