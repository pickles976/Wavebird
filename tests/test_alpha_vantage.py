from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from wavebird.alpha_vantage import AlphaDataset, AlphaVantageClient
from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import (
    AuthenticationError,
    InvalidTickerError,
    ProviderResponseError,
    ProviderSchemaError,
    QuotaError,
    TransportError,
)
from wavebird.models import PeriodKind
from wavebird.valuation import parse_financial_periods, parse_weekly_prices

NOW = datetime(2026, 1, 1, tzinfo=UTC)

STATEMENT_BODY = {"symbol": "IBM", "annualReports": [], "quarterlyReports": []}


def make_cache(tmp_path):
    return SQLiteCache(tmp_path / "cache.sqlite3")


def make_client(tmp_path, handler, api_key="demo", cache=None):
    return AlphaVantageClient(
        cache=cache if cache is not None else SQLiteCache(tmp_path / "client.sqlite3"),
        api_key=api_key,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )


def seed(cache, dataset, symbol, payload, *, fetched=NOW, ttl_days=7):
    key = CacheKey("alpha_vantage", dataset.value, symbol, f"{dataset.value}:{symbol}")
    cache.put(
        key,
        payload,
        fetched,
        fetched + timedelta(days=ttl_days),
        f"https://www.alphavantage.co/query?function={dataset.value}&symbol={symbol}",
    )


def test_fresh_cache_prevents_http(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.BALANCE_SHEET, "IBM", STATEMENT_BODY)
    client = make_client(tmp_path, handler, cache=cache)
    assert client.get(AlphaDataset.BALANCE_SHEET, "IBM").data["symbol"] == "IBM"
    assert calls == 0
    assert cache.requests_today("alpha_vantage", NOW) == 0
    cache.close()


def test_outbound_requests_are_counted(tmp_path) -> None:
    cache = make_cache(tmp_path)
    client = make_client(
        tmp_path,
        lambda request: httpx.Response(200, json=STATEMENT_BODY),
        cache=cache,
    )
    client.get(AlphaDataset.BALANCE_SHEET, "IBM")
    assert cache.requests_today("alpha_vantage", NOW) == 1
    client.get(AlphaDataset.BALANCE_SHEET, "IBM")
    assert cache.requests_today("alpha_vantage", NOW) == 1

    error_client = make_client(
        tmp_path,
        lambda request: httpx.Response(200, json={"Error Message": "Invalid API call"}),
        cache=cache,
    )
    with pytest.raises(InvalidTickerError):
        error_client.get(AlphaDataset.CASH_FLOW, "BAD")
    assert cache.requests_today("alpha_vantage", NOW) == 2
    cache.close()


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"Error Message": "Invalid API call"}, InvalidTickerError),
        ({"Note": "25 requests per day"}, QuotaError),
        ({"Information": "rate limit reached"}, QuotaError),
    ],
)
def test_error_envelopes_are_not_success(body, error_type, tmp_path) -> None:
    client = make_client(tmp_path, lambda request: httpx.Response(200, json=body))
    with pytest.raises(error_type):
        client.get(AlphaDataset.CASH_FLOW, "BAD")


def test_missing_key_on_cache_miss_raises_authentication(tmp_path) -> None:
    client = make_client(
        tmp_path, lambda request: httpx.Response(200, json=STATEMENT_BODY), api_key=None
    )
    with pytest.raises(AuthenticationError):
        client.get(AlphaDataset.BALANCE_SHEET, "IBM")


def test_source_url_never_contains_api_key(tmp_path) -> None:
    client = make_client(tmp_path, lambda request: httpx.Response(200, json=STATEMENT_BODY))
    payload = client.get(AlphaDataset.BALANCE_SHEET, "IBM")
    assert "apikey" not in payload.source.source_url
    assert "demo" not in payload.source.source_url
    assert "function=BALANCE_SHEET" in payload.source.source_url
    assert "symbol=IBM" in payload.source.source_url
    assert payload.source.provider == "alpha_vantage"
    assert payload.source.dataset == "BALANCE_SHEET"
    assert payload.source.symbol == "IBM"
    assert payload.source.stale is False


def test_exception_text_never_contains_api_key(tmp_path) -> None:
    client = make_client(
        tmp_path, lambda request: httpx.Response(200, json={"Error Message": "Invalid"})
    )
    with pytest.raises(InvalidTickerError) as excinfo:
        client.get(AlphaDataset.BALANCE_SHEET, "IBM")
    assert "demo" not in str(excinfo.value)


def test_refresh_bypasses_fresh_row(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=STATEMENT_BODY)

    cache = make_cache(tmp_path)
    stale_body = {"symbol": "OLD", "annualReports": [], "quarterlyReports": []}
    seed(cache, AlphaDataset.BALANCE_SHEET, "IBM", stale_body)
    client = make_client(tmp_path, handler, cache=cache)
    payload = client.get(AlphaDataset.BALANCE_SHEET, "IBM", refresh=True)
    assert calls == 1
    assert payload.data["symbol"] == "IBM"
    cache.close()


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (429, QuotaError),
        (500, TransportError),
        (400, ProviderResponseError),
    ],
)
def test_http_status_mapping(status, error_type, tmp_path) -> None:
    client = make_client(tmp_path, lambda request: httpx.Response(status))
    with pytest.raises(error_type):
        client.get(AlphaDataset.BALANCE_SHEET, "IBM")


def test_non_object_json_is_schema_error(tmp_path) -> None:
    client = make_client(tmp_path, lambda request: httpx.Response(200, json=[1, 2]))
    with pytest.raises(ProviderSchemaError):
        client.get(AlphaDataset.BALANCE_SHEET, "IBM")


def test_stale_fallback_on_timeout(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.BALANCE_SHEET, "IBM", STATEMENT_BODY, fetched=NOW - timedelta(days=30))
    client = make_client(tmp_path, handler, cache=cache)
    payload = client.get(AlphaDataset.BALANCE_SHEET, "IBM")
    assert payload.data["symbol"] == "IBM"
    assert payload.source.stale is True
    cache.close()


def test_stale_fallback_on_quota(tmp_path) -> None:
    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.CASH_FLOW, "IBM", STATEMENT_BODY, fetched=NOW - timedelta(days=30))
    client = make_client(
        tmp_path,
        lambda request: httpx.Response(200, json={"Note": "25 requests per day"}),
        cache=cache,
    )
    payload = client.get(AlphaDataset.CASH_FLOW, "IBM")
    assert payload.data["symbol"] == "IBM"
    assert payload.source.stale is True
    cache.close()


def test_no_stale_fallback_on_invalid_ticker(tmp_path) -> None:
    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.CASH_FLOW, "IBM", STATEMENT_BODY, fetched=NOW - timedelta(days=30))
    client = make_client(
        tmp_path,
        lambda request: httpx.Response(200, json={"Error Message": "Invalid API call"}),
        cache=cache,
    )
    with pytest.raises(InvalidTickerError):
        client.get(AlphaDataset.CASH_FLOW, "IBM")
    cache.close()


def test_no_stale_fallback_on_schema_error(tmp_path) -> None:
    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.CASH_FLOW, "IBM", STATEMENT_BODY, fetched=NOW - timedelta(days=30))
    client = make_client(tmp_path, lambda request: httpx.Response(200, json={}), cache=cache)
    with pytest.raises(ProviderSchemaError):
        client.get(AlphaDataset.CASH_FLOW, "IBM")
    cache.close()


def test_no_stale_fallback_on_authentication(tmp_path) -> None:
    cache = make_cache(tmp_path)
    seed(cache, AlphaDataset.CASH_FLOW, "IBM", STATEMENT_BODY, fetched=NOW - timedelta(days=30))
    client = make_client(tmp_path, lambda request: httpx.Response(401), cache=cache)
    with pytest.raises(AuthenticationError):
        client.get(AlphaDataset.CASH_FLOW, "IBM")
    cache.close()


def test_invalid_symbol_characters_rejected(tmp_path) -> None:
    client = make_client(tmp_path, lambda request: httpx.Response(200, json=STATEMENT_BODY))
    with pytest.raises(InvalidTickerError):
        client.get(AlphaDataset.BALANCE_SHEET, "BAD@TICKER")


def test_symbol_is_normalized(tmp_path) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json=STATEMENT_BODY)

    client = make_client(tmp_path, handler)
    payload = client.get(AlphaDataset.BALANCE_SHEET, "  ibm ")
    assert payload.source.symbol == "IBM"
    assert "symbol=IBM" in payload.source.source_url


# --- Task 5: normalization -------------------------------------------------

ANNUAL_BALANCE_2025 = {
    "fiscalDateEnding": "2025-12-31",
    "reportedCurrency": "USD",
    "totalCurrentAssets": "500",
    "cashAndShortTermInvestments": "100",
    "totalCurrentLiabilities": "300",
    "currentDebt": "50",
    "shortLongTermDebtTotal": "200",
    "totalShareholderEquity": "500",
    "commonStockSharesOutstanding": "20",
}

QUARTER_DATES = [
    "2024-12-31",
    "2025-03-31",
    "2025-06-30",
    "2025-09-30",
    "2025-12-31",
]


def quarter_income(fiscal: str, revenue: str) -> dict:
    return {
        "fiscalDateEnding": fiscal,
        "reportedCurrency": "USD",
        "totalRevenue": revenue,
        "ebit": "15",
        "incomeBeforeTax": "13",
        "incomeTaxExpense": "3",
        "interestExpense": "1",
        "netIncome": "10",
    }


def quarter_cash(fiscal: str) -> dict:
    return {
        "fiscalDateEnding": fiscal,
        "capitalExpenditures": "-7",
        "depreciationDepletionAndAmortization": "4",
        "dividendPayoutCommonStock": "2",
    }


def quarter_balance(fiscal: str) -> dict:
    return {
        "fiscalDateEnding": fiscal,
        "reportedCurrency": "USD",
        "totalCurrentAssets": "500",
        "cashAndShortTermInvestments": "100",
        "totalCurrentLiabilities": "300",
        "currentDebt": "50",
        "shortLongTermDebtTotal": "200",
        "totalShareholderEquity": "500",
        "commonStockSharesOutstanding": "20",
    }


def build_bundle_payloads() -> dict:
    income_annual = [
        {
            "fiscalDateEnding": "2023-12-31",
            "reportedCurrency": "USD",
            "totalRevenue": "800",
            "ebit": "100",
            "incomeBeforeTax": "90",
            "incomeTaxExpense": "19",
            "interestExpense": "7",
            "netIncome": "71",
        },
        {
            "fiscalDateEnding": "2024-12-31",
            "reportedCurrency": "USD",
            "totalRevenue": "900",
            "ebit": "120",
            "incomeBeforeTax": "100",
            "incomeTaxExpense": "21",
            "netIncome": "79",
        },
        {
            "fiscalDateEnding": "2025-12-31",
            "reportedCurrency": "USD",
            "totalRevenue": "1000",
            "ebit": "150",
            "incomeBeforeTax": "130",
            "incomeTaxExpense": "27",
            "interestExpense": "10",
            "netIncome": "103",
        },
    ]
    cash_annual = [
        {
            "fiscalDateEnding": "2025-12-31",
            "capitalExpenditures": "-70",
            "depreciationDepletionAndAmortization": "40",
            "dividendPayoutCommonStock": "20",
        },
        {
            "fiscalDateEnding": "2024-12-31",
            "capitalExpenditures": "-60",
            "depreciationDepletionAndAmortization": "38",
            "dividendPayoutCommonStock": "None",
        },
        {
            "fiscalDateEnding": "2023-12-31",
            "capitalExpenditures": "-55",
            "depreciationDepletionAndAmortization": "36",
            "dividendPayoutCommonStock": "18",
        },
    ]
    balance_annual = [
        dict(ANNUAL_BALANCE_2025, fiscalDateEnding="2025-12-31"),
        {
            "fiscalDateEnding": "2024-12-31",
            "reportedCurrency": "USD",
            "totalCurrentAssets": "450",
            "cashAndShortTermInvestments": "90",
            "totalCurrentLiabilities": "280",
            "currentDebt": "40",
            "shortLongTermDebtTotal": "180",
            "totalShareholderEquity": "480",
            "commonStockSharesOutstanding": "19",
        },
        {
            "fiscalDateEnding": "2023-12-31",
            "reportedCurrency": "USD",
            "totalCurrentAssets": "430",
            "cashAndShortTermInvestments": "80",
            "totalCurrentLiabilities": "260",
            "currentDebt": "35",
            "shortLongTermDebtTotal": "170",
            "totalShareholderEquity": "460",
            "commonStockSharesOutstanding": "18",
        },
    ]
    income_quarterly = [
        quarter_income(fiscal, revenue)
        for fiscal, revenue in zip(QUARTER_DATES, ["100", "110", "120", "130", "140"], strict=True)
    ]
    cash_quarterly = [quarter_cash(fiscal) for fiscal in QUARTER_DATES]
    balance_quarterly = [quarter_balance(fiscal) for fiscal in QUARTER_DATES]
    earnings = {
        "annualEarnings": [
            {"fiscalDateEnding": "2023-12-31", "reportedEPS": "7.0"},
            {"fiscalDateEnding": "2024-12-31", "reportedEPS": "8.0"},
            {"fiscalDateEnding": "2025-12-31", "reportedEPS": "5.15"},
        ],
        "quarterlyEarnings": [
            {"fiscalDateEnding": "2024-12-31", "reportedEPS": "2.0", "reportedDate": "2025-01-28"},
            {"fiscalDateEnding": "2025-03-31", "reportedEPS": "3.0", "reportedDate": "2025-04-29"},
            {"fiscalDateEnding": "2025-06-30", "reportedEPS": "4.0"},
            {"fiscalDateEnding": "2025-09-30", "reportedEPS": "5.0", "reportedDate": "2025-10-28"},
            {"fiscalDateEnding": "2025-12-31", "reportedEPS": "6.0", "reportedDate": "2026-01-28"},
        ],
    }
    return {
        "INCOME_STATEMENT": {"annualReports": income_annual, "quarterlyReports": income_quarterly},
        "CASH_FLOW": {"annualReports": cash_annual, "quarterlyReports": cash_quarterly},
        "BALANCE_SHEET": {
            "annualReports": balance_annual,
            "quarterlyReports": balance_quarterly,
        },
        "EARNINGS": earnings,
    }


def annual_by_date(periods):
    return {period.fiscal_date: period for period in periods}


def test_annual_records_join_by_date_with_signs_and_nwc(tmp_path) -> None:
    periods = parse_financial_periods(build_bundle_payloads(), PeriodKind.ANNUAL)
    by_date = annual_by_date(periods)
    latest = by_date[date(2025, 12, 31)]
    assert latest.capex == 70.0
    assert latest.nwc == (500 - 100) - (300 - 50)
    assert latest.reported_eps == pytest.approx(5.15)
    assert latest.revenue == pytest.approx(1000.0)
    assert latest.shares_outstanding == pytest.approx(20.0)


def test_none_strings_and_missing_fields_become_none(tmp_path) -> None:
    periods = parse_financial_periods(build_bundle_payloads(), PeriodKind.ANNUAL)
    by_date = annual_by_date(periods)
    assert by_date[date(2024, 12, 31)].dividends is None
    assert by_date[date(2024, 12, 31)].interest_expense is None


def test_annual_availability_uses_matching_q4_reported_date(tmp_path) -> None:
    periods = parse_financial_periods(build_bundle_payloads(), PeriodKind.ANNUAL)
    by_date = annual_by_date(periods)
    assert by_date[date(2025, 12, 31)].availability_date == date(2026, 1, 28)
    assert by_date[date(2024, 12, 31)].availability_date == date(2025, 1, 28)


def test_annual_availability_falls_back_to_plus_ninety_days(tmp_path) -> None:
    periods = parse_financial_periods(build_bundle_payloads(), PeriodKind.ANNUAL)
    by_date = annual_by_date(periods)
    assert by_date[date(2023, 12, 31)].availability_date == date(2024, 3, 30)


def test_quarter_without_reported_date_falls_back_to_plus_forty_five_days(tmp_path) -> None:
    periods = parse_financial_periods(build_bundle_payloads(), PeriodKind.QUARTER)
    by_date = {period.fiscal_date: period for period in periods}
    assert by_date[date(2025, 6, 30)].availability_date == date(2025, 8, 14)
    assert by_date[date(2025, 9, 30)].availability_date == date(2025, 10, 28)
    assert len(periods) == 5


def test_non_usd_records_are_rejected(tmp_path) -> None:
    payloads = build_bundle_payloads()
    payloads["INCOME_STATEMENT"]["annualReports"].append(
        {
            "fiscalDateEnding": "2022-12-31",
            "reportedCurrency": "EUR",
            "totalRevenue": "700",
            "ebit": "90",
            "incomeBeforeTax": "80",
            "incomeTaxExpense": "17",
            "interestExpense": "6",
            "netIncome": "63",
        }
    )
    periods = parse_financial_periods(payloads, PeriodKind.ANNUAL)
    assert date(2022, 12, 31) not in annual_by_date(periods)


def test_weekly_prices_are_sorted_and_positive(tmp_path) -> None:
    payload = {
        "Weekly Adjusted Time Series": {
            "2025-01-10": {"4. close": "12", "5. adjusted close": "11.4"},
            "2025-01-03": {"4. close": "10", "5. adjusted close": "9.5"},
            "2025-01-17": {"4. close": "14", "5. adjusted close": "13.3"},
        }
    }
    points = parse_weekly_prices(payload)
    assert [point.date for point in points] == [
        date(2025, 1, 3),
        date(2025, 1, 10),
        date(2025, 1, 17),
    ]
    with pytest.raises(ProviderSchemaError):
        parse_weekly_prices(
            {
                "Weekly Adjusted Time Series": {
                    "2025-01-03": {"4. close": "0", "5. adjusted close": "9.5"}
                }
            }
        )


def test_spy_weekly_caches_for_seven_days(tmp_path) -> None:
    from conftest import SPY_WEEKLY

    cache = make_cache(tmp_path)
    client = make_client(
        tmp_path,
        lambda request: httpx.Response(200, json=SPY_WEEKLY),
        cache=cache,
    )
    client.get(AlphaDataset.WEEKLY_ADJUSTED, "SPY")
    record = cache.get(
        CacheKey(
            "alpha_vantage",
            "TIME_SERIES_WEEKLY_ADJUSTED",
            "SPY",
            "TIME_SERIES_WEEKLY_ADJUSTED:SPY",
        )
    )
    assert record is not None
    assert record.expires_at - record.fetched_at == timedelta(days=7)
    cache.close()
