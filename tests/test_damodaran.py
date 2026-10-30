from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from wavebird.cache import SQLiteCache
from wavebird.damodaran import (
    ANNUAL_KEY,
    MONTHLY_KEY,
    DamodaranClient,
    parse_annual_frame,
    parse_monthly_frame,
)
from wavebird.errors import DataUnavailableError, ProviderSchemaError, TransportError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_annual_uses_named_damodaran_columns() -> None:
    frame = pd.DataFrame(
        [
            ["metadata", None, None],
            ["Year", "T.Bond Rate", "Implied ERP (FCFE)"],
            [2024, 0.0438, 0.0433],
        ]
    )
    point = parse_annual_frame(frame)[0]
    assert point.date == date(2024, 12, 31)
    assert point.risk_free_rate == 0.0438
    assert point.equity_risk_premium == 0.0433


def test_parse_monthly_converts_excel_serial() -> None:
    frame = pd.DataFrame(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12m)"],
            [45292, 0.0388, 0.0460],
        ]
    )
    assert parse_monthly_frame(frame)[0].date == date(2024, 1, 1)


def test_parse_monthly_converts_percent_strings_to_decimals() -> None:
    frame = pd.DataFrame(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12m)"],
            ["1-Sep-24", "3.90%", "4.60%"],
        ]
    )
    point = parse_monthly_frame(frame)[0]
    assert point.risk_free_rate == pytest.approx(0.039)
    assert point.equity_risk_premium == pytest.approx(0.046)


def test_parse_monthly_parses_string_dates() -> None:
    frame = pd.DataFrame(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12m)"],
            ["1-Sep-24", 0.0388, 0.0460],
        ]
    )
    assert parse_monthly_frame(frame)[0].date == date(2024, 9, 1)


def test_parse_monthly_string_dates_resolve_last_century() -> None:
    frame = pd.DataFrame(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12m)"],
            ["1-Jan-65", 0.0421, 0.0309],
        ]
    )
    assert parse_monthly_frame(frame)[0].date == date(1965, 1, 1)


def test_column_drift_fails_loudly() -> None:
    frame = pd.DataFrame([["Year", "Treasury", "Mystery ERP"], [2024, 0.04, 0.05]])
    with pytest.raises(ProviderSchemaError, match=r"T\.Bond Rate"):
        parse_annual_frame(frame)


def seed_catalog(cache, key, records, *, fetched=NOW, ttl_days=30):
    cache.put(
        key,
        {"records": records},
        fetched,
        fetched + timedelta(days=ttl_days),
        "https://pages.stern.nyu.edu/~adamodar/pc/datasets/test",
    )


def make_client(cache, handler):
    return DamodaranClient(
        cache=cache,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        annual_url="https://example.test/annual.xls",
        monthly_url="https://example.test/monthly.xlsx",
        now=lambda: NOW,
    )


def test_monthly_preferred_when_available(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    seed_catalog(
        cache,
        ANNUAL_KEY,
        [{"date": "2024-12-31", "risk_free_rate": 0.0438, "equity_risk_premium": 0.0433}],
        ttl_days=30,
    )
    seed_catalog(
        cache,
        MONTHLY_KEY,
        [{"date": "2025-01-01", "risk_free_rate": 0.0455, "equity_risk_premium": 0.0450}],
        ttl_days=7,
    )
    client = make_client(cache, lambda request: httpx.Response(500))
    point = client.get_rates(date(2025, 6, 30))
    assert point.date == date(2025, 1, 1)
    assert point.risk_free_rate == 0.0455
    assert point.source.dataset == "monthly_rates"
    cache.close()


def test_annual_used_for_older_dates(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    seed_catalog(
        cache,
        ANNUAL_KEY,
        [
            {"date": "2022-12-31", "risk_free_rate": 0.0383, "equity_risk_premium": 0.0506},
            {"date": "2024-12-31", "risk_free_rate": 0.0438, "equity_risk_premium": 0.0433},
        ],
    )
    seed_catalog(
        cache,
        MONTHLY_KEY,
        [{"date": "2025-01-01", "risk_free_rate": 0.0455, "equity_risk_premium": 0.0450}],
        ttl_days=7,
    )
    client = make_client(cache, lambda request: httpx.Response(500))
    point = client.get_rates(date(2023, 6, 30))
    assert point.date == date(2022, 12, 31)
    assert point.source.dataset == "annual_rates"
    cache.close()


def test_no_future_row_is_selected(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    seed_catalog(
        cache,
        ANNUAL_KEY,
        [{"date": "2024-12-31", "risk_free_rate": 0.0438, "equity_risk_premium": 0.0433}],
    )
    seed_catalog(
        cache,
        MONTHLY_KEY,
        [{"date": "2025-01-01", "risk_free_rate": 0.0455, "equity_risk_premium": 0.0450}],
        ttl_days=7,
    )
    client = make_client(cache, lambda request: httpx.Response(500))
    with pytest.raises(DataUnavailableError):
        client.get_rates(date(2023, 1, 1))
    cache.close()


def test_expired_catalog_used_on_transport_failure(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    seed_catalog(
        cache,
        ANNUAL_KEY,
        [{"date": "2024-12-31", "risk_free_rate": 0.0438, "equity_risk_premium": 0.0433}],
        fetched=NOW - timedelta(days=60),
        ttl_days=30,
    )
    client = make_client(
        cache, lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out"))
    )
    point = client.get_rates(date(2025, 6, 30))
    assert point.date == date(2024, 12, 31)
    assert point.source.stale is True
    cache.close()


def test_transport_failure_without_any_catalog_raises(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    client = make_client(cache, lambda request: httpx.Response(500))
    with pytest.raises(TransportError):
        client.get_rates(date(2025, 6, 30))
    cache.close()


def test_fresh_catalog_prevents_http(tmp_path) -> None:
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    seed_catalog(
        cache,
        ANNUAL_KEY,
        [{"date": "2024-12-31", "risk_free_rate": 0.0438, "equity_risk_premium": 0.0433}],
    )
    seed_catalog(
        cache,
        MONTHLY_KEY,
        [{"date": "2025-01-01", "risk_free_rate": 0.0455, "equity_risk_premium": 0.0450}],
        ttl_days=7,
    )
    client = make_client(cache, handler)
    point = client.get_rates(date(2025, 6, 30))
    assert calls == 0
    assert point.source.stale is False
    assert point.source.dataset == "monthly_rates"
    cache.close()


def test_annual_footer_rows_are_skipped() -> None:
    frame = pd.DataFrame(
        [
            ["Year", "T.Bond Rate", "Implied ERP (FCFE)"],
            [2024, 0.0438, 0.0433],
            [
                "* Earnings and dividends numbers each year reflect the estimated "
                "numbers as of the end of the year.",
                None,
                None,
            ],
        ]
    )
    records = parse_annual_frame(frame)
    assert len(records) == 1
    assert records[0].date == date(2024, 12, 31)


def test_missing_rate_rows_are_skipped() -> None:
    frame = pd.DataFrame(
        [
            ["Year", "T.Bond Rate", "Implied ERP (FCFE)"],
            [1960, float("nan"), float("nan")],
            [2024, 0.0438, 0.0433],
        ]
    )
    records = parse_annual_frame(frame)
    assert len(records) == 1
    assert records[0].date == date(2024, 12, 31)


def test_invalid_rate_values_are_rejected(tmp_path) -> None:
    frame = pd.DataFrame(
        [
            ["Year", "T.Bond Rate", "Implied ERP (FCFE)"],
            [2024, 1.5, 0.0433],
        ]
    )
    with pytest.raises(ProviderSchemaError):
        parse_annual_frame(frame)
