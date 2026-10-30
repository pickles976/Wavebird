from datetime import date

import pytest
from conftest import (
    ANNUAL_FISCAL_DATES,
    QUARTER_FISCAL_DATES,
    FakeAlphaProvider,
    FakeRatesProvider,
    build_fake_payloads,
)

from wavebird.alpha_vantage import AlphaDataset
from wavebird.config import ValuationAssumptions
from wavebird.errors import DataUnavailableError, ValuationMathError
from wavebird.models import MarketInputs, ModelName, PeriodKind, ScreenState
from wavebird.service import AnalysisService
from wavebird.valuation import calculate_stable_fcfe

COMPANY_DATASETS = {
    AlphaDataset.INCOME_STATEMENT.value,
    AlphaDataset.CASH_FLOW.value,
    AlphaDataset.BALANCE_SHEET.value,
    AlphaDataset.EARNINGS.value,
    AlphaDataset.WEEKLY_ADJUSTED.value,
}


def test_screen_inputs_preserve_each_model_value(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    assert report.symbol == "ACME"
    assert {result.model for result in report.current.models} == set(ModelName)
    assert len(report.current.models) == 3
    assert report.current.market_price > 0


def test_fetch_bundle_requests_exact_datasets_and_dated_rates(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    alpha_calls = fake_service.alpha.calls
    company_calls = [call for call in alpha_calls if call[1] == "ACME"]
    spy_calls = [call for call in alpha_calls if call[1] == "SPY"]
    assert {call[0] for call in company_calls} == COMPANY_DATASETS
    assert len(company_calls) == 5
    assert len(spy_calls) == 1
    assert spy_calls[0][0] == AlphaDataset.WEEKLY_ADJUSTED.value

    expected_dates = {report.as_of}
    for point in (*report.annual_history, *report.ttm_history):
        expected_dates.add(point.market.as_of)
    rate_dates = {call[0] for call in fake_service.rates.calls}
    assert rate_dates == expected_dates
    assert len(fake_service.rates.calls) == len(expected_dates)


def test_refresh_reaches_every_alpha_call_and_first_rate_only(fake_service, assumptions) -> None:
    fake_service.analyze("acme", assumptions, date(2026, 2, 1), refresh=True)
    assert all(call[2] is True for call in fake_service.alpha.calls)
    refresh_flags = [call[1] for call in fake_service.rates.calls]
    assert refresh_flags.count(True) == 1
    assert refresh_flags[0] is True


def test_future_availability_excluded_even_when_fiscal_past(assumptions) -> None:
    payloads = build_fake_payloads(q4_2025_reported_date=date(2026, 2, 15))
    service = AnalysisService(alpha=FakeAlphaProvider(payloads), rates=FakeRatesProvider())
    report = service.analyze("acme", assumptions, date(2026, 2, 1))
    annual_fiscals = [point.period.fiscal_date for point in report.annual_history]
    assert ANNUAL_FISCAL_DATES[-1] not in annual_fiscals
    assert report.current.period.fiscal_date == QUARTER_FISCAL_DATES[-2]


def test_invalid_currency_is_rejected(fake_payloads, assumptions) -> None:
    for record in fake_payloads[AlphaDataset.INCOME_STATEMENT]["annualReports"]:
        record["reportedCurrency"] = "EUR"
    for record in fake_payloads[AlphaDataset.INCOME_STATEMENT]["quarterlyReports"]:
        record["reportedCurrency"] = "EUR"
    service = AnalysisService(alpha=FakeAlphaProvider(fake_payloads), rates=FakeRatesProvider())
    with pytest.raises(DataUnavailableError):
        service.analyze("acme", assumptions, date(2026, 2, 1))


def test_one_model_unavailable_keeps_others_valid(fake_payloads, assumptions) -> None:
    fake_payloads[AlphaDataset.EARNINGS] = {"annualEarnings": [], "quarterlyEarnings": []}
    service = AnalysisService(alpha=FakeAlphaProvider(fake_payloads), rates=FakeRatesProvider())
    report = service.analyze("acme", assumptions, date(2026, 2, 1))
    results = {result.model: result for result in report.current.models}
    assert results[ModelName.TWO_STAGE_FCFF].unavailable_reason is None
    assert results[ModelName.STABLE_FCFE].unavailable_reason is not None
    assert results[ModelName.TWO_STAGE_FCFE].unavailable_reason is not None
    assert report.screen_state is ScreenState.INCONCLUSIVE


def test_stale_source_metadata_creates_warning(fake_payloads, assumptions) -> None:
    alpha = FakeAlphaProvider(fake_payloads, stale_datasets={AlphaDataset.EARNINGS})
    service = AnalysisService(alpha=alpha, rates=FakeRatesProvider())
    report = service.analyze("acme", assumptions, date(2026, 2, 1))
    assert any("STALE" in warning and "EARNINGS" in warning for warning in report.warnings)


def test_market_cap_uses_adjusted_close_and_prior_shares(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    current = report.current
    assert current.market_capitalization == pytest.approx(
        current.market_price * current.period.shares_outstanding
    )
    first_share_availability = min(
        point.period.availability_date for point in (*report.annual_history, *report.ttm_history)
    )
    assert report.weekly_market_cap
    assert all(point.date >= first_share_availability for point in report.weekly_market_cap)
    for point in report.weekly_market_cap:
        assert point.market_capitalization > 0
        # Alpha restates share counts to the modern share basis, so the weekly series
        # must pair adjusted (split-adjusted) closes with them, never raw closes.
        assert point.market_price > 0


def test_weekly_market_price_is_split_adjusted_close(fake_service, assumptions) -> None:
    from wavebird.alpha_vantage import AlphaDataset

    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    payload = fake_service.alpha.payloads[AlphaDataset.WEEKLY_ADJUSTED]
    series = payload["Weekly Adjusted Time Series"]
    assert report.weekly_market_cap
    latest_week = max(series)
    point = next(p for p in report.weekly_market_cap if p.date.isoformat() == latest_week)
    week = series[latest_week]
    assert point.market_price == pytest.approx(float(week["5. adjusted close"]))
    balance = fake_service.alpha.payloads[AlphaDataset.BALANCE_SHEET]
    # The series pairs the prior share observation available by the price date,
    # annuals before quarters on equal availability (service tie-break order).
    from conftest import _availability

    week_date = date.fromisoformat(latest_week)
    observations = []
    for kind in ("annualReports", "quarterlyReports"):
        for report_row in balance[kind]:
            if not report_row.get("commonStockSharesOutstanding"):
                continue
            availability = _availability(date.fromisoformat(report_row["fiscalDateEnding"]))
            if availability <= week_date:
                observations.append(
                    (availability, float(report_row["commonStockSharesOutstanding"]))
                )
    shares = max(observations, key=lambda item: item[0])[1]
    assert shares > 0
    assert point.market_capitalization == pytest.approx(float(week["5. adjusted close"]) * shares)


def test_rates_and_beta_use_availability_date(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    historical_dates = {
        point.market.as_of for point in (*report.annual_history, *report.ttm_history)
    }
    rate_dates = {call[0] for call in fake_service.rates.calls}
    assert historical_dates <= rate_dates
    assert report.current.market.as_of == report.as_of
    for point in (*report.annual_history, *report.ttm_history):
        assert point.market.as_of == point.period.availability_date
        assert point.market_price > 0


def test_annual_and_ttm_histories_remain_separate(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    assert len(report.annual_history) == 3
    assert all(point.period.period_kind is PeriodKind.ANNUAL for point in report.annual_history)
    assert report.ttm_history
    assert all(point.period.period_kind is PeriodKind.TTM for point in report.ttm_history)


def test_current_point_is_latest_available_ttm(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    ttm_fiscals = [point.period.fiscal_date for point in report.ttm_history]
    assert report.current.period.fiscal_date == max(ttm_fiscals)
    assert report.current.period.fiscal_date == QUARTER_FISCAL_DATES[-1]


def test_sensitivity_grid_has_nine_cells_per_model(analysis_report) -> None:
    report = analysis_report
    assert len(report.current.sensitivity) == 27
    base = {result.model: result.value_per_share for result in report.current.models}
    for model in ModelName:
        cells = [point for point in report.current.sensitivity if point.model is model]
        assert len(cells) == 9
        center = next(
            point
            for point in cells
            if point.discount_rate_delta == 0.0 and point.terminal_growth_delta == 0.0
        )
        assert center.value_per_share == pytest.approx(base[model])


def test_screen_passes_when_all_models_show_discount(analysis_report) -> None:
    report = analysis_report
    assert report.screen_state is ScreenState.PASSES
    for result in report.current.models:
        assert result.unavailable_reason is None
        assert result.discount_to_value is not None
        assert result.discount_to_value >= report.assumptions.margin_of_safety


def test_invalid_sensitivity_cell_stores_reason_instead_of_clamping() -> None:
    from wavebird.models import FinancialPeriod, PeriodKind

    period = FinancialPeriod(
        symbol="ACME",
        fiscal_date=date(2025, 12, 31),
        availability_date=date(2026, 1, 20),
        period_kind=PeriodKind.TTM,
        reported_currency="USD",
        reported_eps=5.0,
        revenue=10_000.0,
        ebit=1_500.0,
        pretax_income=1_300.0,
        tax_expense=273.0,
        capex=700.0,
        depreciation=400.0,
        cash=1_000.0,
        total_debt=2_000.0,
        book_equity=5_000.0,
        shares_outstanding=200.0,
        nwc=1_000.0,
        delta_nwc=100.0,
    )
    market = MarketInputs(
        as_of=date(2026, 2, 1),
        beta=1.0,
        risk_free_rate=0.065,
        equity_risk_premium=0.02,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.06,
        stable_growth_rate=0.08,
        stable_roe=0.12,
        stable_roc=0.10,
        stable_beta=1.0,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.20,
        margin_of_safety=0.25,
    )
    service = AnalysisService(
        alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
    )
    grid = service.sensitivity_for(
        ModelName.STABLE_FCFE, period, market, assumptions, tax_rate=None
    )
    assert len(grid) == 9
    bottom_row = [cell for cell in grid if cell.discount_rate_delta == -0.01]
    assert all(cell.unavailable_reason is not None for cell in bottom_row)
    center = next(
        cell
        for cell in grid
        if cell.discount_rate_delta == 0.0 and cell.terminal_growth_delta == 0.0
    )
    base = calculate_stable_fcfe(period, market, assumptions)
    assert center.value_per_share == pytest.approx(base.value_per_share)
    with pytest.raises(ValuationMathError):
        shifted = MarketInputs(
            as_of=market.as_of,
            beta=market.beta,
            risk_free_rate=market.risk_free_rate - 0.01,
            equity_risk_premium=market.equity_risk_premium,
        )
        lowered = ValuationAssumptions.model_validate(
            {
                **assumptions.model_dump(),
                "stable_growth_rate": assumptions.stable_growth_rate + 0.005,
            }
        )
        calculate_stable_fcfe(period, shifted, lowered)


def test_weekly_market_cap_pairs_nearest_shares_observation() -> None:
    from conftest import FakeAlphaProvider, FakeRatesProvider, build_fake_payloads

    from wavebird.models import PricePoint

    service = AnalysisService(
        alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
    )
    prices = (
        PricePoint(date=date(2022, 6, 24), close=2200.0, adjusted_close=110.0),
        PricePoint(date=date(2022, 7, 8), close=110.0, adjusted_close=110.0),
    )

    class Period:
        def __init__(self, availability, shares):
            self.availability_date = availability
            self.shares_outstanding = shares

    periods = [
        Period(date(2021, 12, 31), 600_000_000.0),
        Period(date(2022, 12, 31), 12_000_000_000.0),
    ]
    caps = service._weekly_market_cap(prices, periods)
    # Alpha restates historical share counts to the modern share basis, so the
    # adjusted (split-adjusted) closes pair with the prior share observation and the
    # series stays continuous across a split instead of jumping by the split factor.
    assert caps[0].market_capitalization == 110.0 * 600_000_000.0
    assert caps[1].market_capitalization == 110.0 * 600_000_000.0
    assert caps[0].market_capitalization == caps[1].market_capitalization
