import copy
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import matplotlib
import pytest

from wavebird.alpha_vantage import AlphaDataset, ProviderPayload
from wavebird.config import ValuationAssumptions
from wavebird.models import FinancialPeriod, PeriodKind, RatePoint, SourceMetadata

matplotlib.use("Agg")


@pytest.fixture
def assumptions() -> ValuationAssumptions:
    return ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.06,
        stable_growth_rate=0.025,
        stable_roe=0.12,
        stable_roc=0.10,
        stable_beta=1.0,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.20,
        margin_of_safety=0.25,
    )


def _make_period(**overrides: object) -> FinancialPeriod:
    values: dict[str, object] = {
        "symbol": "ACME",
        "fiscal_date": date(2025, 12, 31),
        "availability_date": date(2026, 1, 28),
        "period_kind": PeriodKind.TTM,
        "reported_currency": "USD",
        "reported_eps": 5.0,
        "revenue": 10_000.0,
        "ebit": 1_500.0,
        "pretax_income": 1_300.0,
        "tax_expense": 273.0,
        "interest_expense": 100.0,
        "net_income": 1_027.0,
        "capex": 700.0,
        "depreciation": 400.0,
        "cash": 1_000.0,
        "total_debt": 2_000.0,
        "book_equity": 5_000.0,
        "shares_outstanding": 200.0,
        "nwc": 1_000.0,
        "delta_nwc": 100.0,
    }
    values.update(overrides)
    return FinancialPeriod.model_validate(values)


@pytest.fixture
def make_period() -> Callable[..., FinancialPeriod]:
    return _make_period


# --- Task 10: deterministic fake providers ----------------------------------

FAKE_NOW = datetime(2026, 2, 1, tzinfo=UTC)

QUARTER_FISCAL_DATES = [
    date(2023, 12, 31),
    date(2024, 3, 31),
    date(2024, 6, 30),
    date(2024, 9, 30),
    date(2024, 12, 31),
    date(2025, 3, 31),
    date(2025, 6, 30),
    date(2025, 9, 30),
    date(2025, 12, 31),
]

ANNUAL_FISCAL_DATES = [date(2023, 12, 31), date(2024, 12, 31), date(2025, 12, 31)]


def _availability(fiscal: date) -> date:
    return fiscal + timedelta(days=20)


def _fake_weekly(symbol: str, base: float, phase: float) -> dict[str, object]:
    series: dict[str, dict[str, str]] = {}
    day = date(2022, 12, 16)
    index = 0
    while day <= date(2026, 1, 30):
        close = base * math.exp(0.0012 * index + 0.03 * math.sin(0.7 * index + phase))
        adjusted = close / (1 + 0.001 * index)
        series[day.isoformat()] = {
            "4. close": f"{close:.6f}",
            "5. adjusted close": f"{adjusted:.6f}",
        }
        day += timedelta(days=7)
        index += 1
    return {"Weekly Adjusted Time Series": series}


def _quarter_income(fiscal: date, k: int) -> dict[str, str]:
    revenue = 1000.0 + 10.0 * k
    ebit = revenue * 0.30
    pretax = revenue * 0.13
    tax = pretax * 0.27
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "reportedCurrency": "USD",
        "totalRevenue": f"{revenue}",
        "ebit": f"{ebit}",
        "incomeBeforeTax": f"{pretax}",
        "incomeTaxExpense": f"{tax}",
        "interestExpense": "10",
        "netIncome": f"{pretax - tax}",
    }


def _quarter_cash(fiscal: date) -> dict[str, str]:
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "capitalExpenditures": "-70",
        "depreciationDepletionAndAmortization": "40",
        "dividendPayoutCommonStock": "20",
    }


def _quarter_balance(fiscal: date, k: int) -> dict[str, str]:
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "reportedCurrency": "USD",
        "totalCurrentAssets": f"{500 + 2 * k}",
        "cashAndShortTermInvestments": "100",
        "totalCurrentLiabilities": "300",
        "currentDebt": "50",
        "shortLongTermDebtTotal": "200",
        "totalShareholderEquity": f"{900 + 5 * k}",
        "commonStockSharesOutstanding": f"{95 + 0.1 * k}",
    }


def _annual_income(fiscal: date, revenue: float, eps: float) -> dict[str, str]:
    ebit = revenue * 0.30
    pretax = revenue * 0.13
    tax = pretax * 0.27
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "reportedCurrency": "USD",
        "totalRevenue": f"{revenue}",
        "ebit": f"{ebit}",
        "incomeBeforeTax": f"{pretax}",
        "incomeTaxExpense": f"{tax}",
        "interestExpense": "40",
        "netIncome": f"{pretax - tax}",
        "reportedEPS": f"{eps}",
    }


def _annual_cash(fiscal: date) -> dict[str, str]:
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "capitalExpenditures": "-300",
        "depreciationDepletionAndAmortization": "170",
        "dividendPayoutCommonStock": "90",
    }


def _annual_balance(fiscal: date) -> dict[str, str]:
    return {
        "fiscalDateEnding": fiscal.isoformat(),
        "reportedCurrency": "USD",
        "totalCurrentAssets": "620",
        "cashAndShortTermInvestments": "800",
        "totalCurrentLiabilities": "300",
        "currentDebt": "100",
        "shortLongTermDebtTotal": "2000",
        "totalShareholderEquity": "5000",
        "commonStockSharesOutstanding": "96",
    }


def build_fake_payloads(*, q4_2025_reported_date: date | None = None) -> dict[AlphaDataset, dict]:
    income_quarterly = [_quarter_income(fiscal, k) for k, fiscal in enumerate(QUARTER_FISCAL_DATES)]
    cash_quarterly = [_quarter_cash(fiscal) for fiscal in QUARTER_FISCAL_DATES]
    balance_quarterly = [
        _quarter_balance(fiscal, k) for k, fiscal in enumerate(QUARTER_FISCAL_DATES)
    ]
    quarterly_earnings = [
        {
            "fiscalDateEnding": fiscal.isoformat(),
            "reportedEPS": f"{2.0 + 0.25 * k}",
            "reportedDate": _availability(fiscal).isoformat(),
        }
        for k, fiscal in enumerate(QUARTER_FISCAL_DATES)
    ]
    if q4_2025_reported_date is not None:
        quarterly_earnings[-1]["reportedDate"] = q4_2025_reported_date.isoformat()

    annual_revenues = [5000.0, 5200.0, 5400.0]
    annual_eps = [20.0, 21.0, 22.0]
    income_annual = [
        _annual_income(fiscal, revenue, eps)
        for fiscal, revenue, eps in zip(
            ANNUAL_FISCAL_DATES, annual_revenues, annual_eps, strict=True
        )
    ]
    cash_annual = [_annual_cash(fiscal) for fiscal in ANNUAL_FISCAL_DATES]
    balance_annual = [_annual_balance(fiscal) for fiscal in ANNUAL_FISCAL_DATES]
    annual_earnings = [
        {"fiscalDateEnding": fiscal.isoformat(), "reportedEPS": f"{eps}"}
        for fiscal, eps in zip(ANNUAL_FISCAL_DATES, annual_eps, strict=True)
    ]

    return {
        AlphaDataset.INCOME_STATEMENT: {
            "symbol": "ACME",
            "annualReports": income_annual,
            "quarterlyReports": income_quarterly,
        },
        AlphaDataset.CASH_FLOW: {
            "symbol": "ACME",
            "annualReports": cash_annual,
            "quarterlyReports": cash_quarterly,
        },
        AlphaDataset.BALANCE_SHEET: {
            "symbol": "ACME",
            "annualReports": balance_annual,
            "quarterlyReports": balance_quarterly,
        },
        AlphaDataset.EARNINGS: {
            "annualEarnings": annual_earnings,
            "quarterlyEarnings": quarterly_earnings,
        },
        AlphaDataset.WEEKLY_ADJUSTED: _fake_weekly("ACME", 20.0, 0.0),
    }


SPY_WEEKLY = _fake_weekly("SPY", 400.0, 1.3)


class FakeAlphaProvider:
    def __init__(
        self,
        payloads: dict[AlphaDataset, dict],
        *,
        stale_datasets: set[AlphaDataset] | None = None,
    ) -> None:
        self.payloads = payloads
        self.stale_datasets = stale_datasets or set()
        self.calls: list[tuple[str, str, bool]] = []

    def get(self, dataset: AlphaDataset, symbol: str, refresh: bool = False) -> ProviderPayload:
        self.calls.append((dataset.value, symbol, refresh))
        data = SPY_WEEKLY if symbol == "SPY" else self.payloads[dataset]
        source = SourceMetadata(
            provider="alpha_vantage",
            dataset=dataset.value,
            symbol=symbol,
            source_url=f"fake://{dataset.value}/{symbol}",
            fetched_at=FAKE_NOW,
            expires_at=FAKE_NOW,
            stale=dataset in self.stale_datasets,
        )
        return ProviderPayload(data=copy.deepcopy(data), source=source)


class FakeRatesProvider:
    def __init__(
        self,
        risk_free_rate: float = 0.04,
        equity_risk_premium: float = 0.05,
    ) -> None:
        self.risk_free_rate = risk_free_rate
        self.equity_risk_premium = equity_risk_premium
        self.calls: list[tuple[date, bool]] = []

    def get_rates(self, as_of: date, refresh: bool = False) -> RatePoint:
        self.calls.append((as_of, refresh))
        source = SourceMetadata(
            provider="damodaran",
            dataset="monthly_rates",
            symbol="",
            source_url="fake://damodaran/monthly",
            fetched_at=FAKE_NOW,
            expires_at=FAKE_NOW,
            stale=False,
        )
        return RatePoint(
            date=as_of,
            risk_free_rate=self.risk_free_rate,
            equity_risk_premium=self.equity_risk_premium,
            source=source,
        )


@pytest.fixture
def fake_payloads() -> dict[AlphaDataset, dict]:
    return build_fake_payloads()


@pytest.fixture
def fake_service(fake_payloads):
    from wavebird.service import AnalysisService

    return AnalysisService(alpha=FakeAlphaProvider(fake_payloads), rates=FakeRatesProvider())


@pytest.fixture
def analysis_report(fake_service, assumptions):
    from wavebird.models import AnalysisReport

    report: AnalysisReport = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    return report
