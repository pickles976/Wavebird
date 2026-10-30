from datetime import date, timedelta

import pytest

from wavebird.config import ValuationAssumptions
from wavebird.errors import DataUnavailableError, ValuationMathError
from wavebird.models import FinancialPeriod, MarketInputs, ModelName, PeriodKind, PricePoint
from wavebird.valuation import (
    build_ttm_periods,
    calculate_beta,
    calculate_stable_fcfe,
    calculate_two_stage_fcfe,
    calculate_two_stage_fcff,
    derive_effective_tax_rate,
)


def test_beta_aligns_dates_before_calculation() -> None:
    stock = (
        PricePoint(date=date(2025, 1, 3), close=10, adjusted_close=10),
        PricePoint(date=date(2025, 1, 10), close=12, adjusted_close=12),
        PricePoint(date=date(2025, 1, 17), close=16.8, adjusted_close=16.8),
    )
    spy = (
        PricePoint(date=date(2025, 1, 3), close=100, adjusted_close=100),
        PricePoint(date=date(2025, 1, 10), close=110, adjusted_close=110),
        PricePoint(date=date(2025, 1, 17), close=132, adjusted_close=132),
        PricePoint(date=date(2025, 1, 24), close=145, adjusted_close=145),
    )
    assert calculate_beta(stock, spy, date(2025, 1, 17), min_observations=2) == pytest.approx(2.0)


def test_beta_requires_enough_observations() -> None:
    stock = (
        PricePoint(date=date(2025, 1, 3), close=10, adjusted_close=10),
        PricePoint(date=date(2025, 1, 10), close=12, adjusted_close=12),
        PricePoint(date=date(2025, 1, 17), close=16.8, adjusted_close=16.8),
    )
    spy = (
        PricePoint(date=date(2025, 1, 3), close=100, adjusted_close=100),
        PricePoint(date=date(2025, 1, 10), close=110, adjusted_close=110),
        PricePoint(date=date(2025, 1, 17), close=132, adjusted_close=132),
    )
    with pytest.raises(ValuationMathError):
        calculate_beta(stock, spy, date(2025, 1, 17))


def test_beta_rejects_zero_benchmark_variance() -> None:
    stock = (
        PricePoint(date=date(2025, 1, 3), close=10, adjusted_close=10),
        PricePoint(date=date(2025, 1, 10), close=12, adjusted_close=12),
        PricePoint(date=date(2025, 1, 17), close=16.8, adjusted_close=16.8),
    )
    spy = (
        PricePoint(date=date(2025, 1, 3), close=100, adjusted_close=100),
        PricePoint(date=date(2025, 1, 10), close=100, adjusted_close=100),
        PricePoint(date=date(2025, 1, 17), close=100, adjusted_close=100),
    )
    with pytest.raises(ValuationMathError):
        calculate_beta(stock, spy, date(2025, 1, 17), min_observations=2)


def make_quarter(fiscal: date, revenue: float, eps: float, nwc: float) -> FinancialPeriod:
    return FinancialPeriod(
        symbol="ACME",
        fiscal_date=fiscal,
        availability_date=fiscal + timedelta(days=45),
        period_kind=PeriodKind.QUARTER,
        reported_currency="USD",
        reported_eps=eps,
        revenue=revenue,
        ebit=revenue * 0.15,
        pretax_income=revenue * 0.13,
        tax_expense=revenue * 0.03,
        interest_expense=1.0,
        net_income=revenue * 0.10,
        capex=7.0,
        depreciation=4.0,
        current_assets=500.0,
        cash=100.0,
        current_liabilities=300.0,
        current_debt=50.0,
        total_debt=200.0,
        book_equity=500.0,
        shares_outstanding=20.0,
        nwc=nwc,
        delta_nwc=None,
    )


def build_five_quarters():
    revenues = [100.0, 110.0, 120.0, 130.0, 140.0]
    eps = [1.0, 2.0, 3.0, 4.0, 5.0]
    nwc = [50.0, 55.0, 61.0, 68.0, 76.0]
    quarter_ends = [
        date(2024, 9, 30),
        date(2024, 12, 31),
        date(2025, 3, 31),
        date(2025, 6, 30),
        date(2025, 9, 30),
    ]
    return tuple(
        make_quarter(fiscal, revenue, eps_value, nwc_value)
        for fiscal, revenue, eps_value, nwc_value in zip(
            quarter_ends, revenues, eps, nwc, strict=True
        )
    )


def test_ttm_sums_flows_and_sets_delta_nwc() -> None:
    quarters = build_five_quarters()
    ttms = build_ttm_periods(quarters)
    assert len(ttms) == 1
    ttm = ttms[0]
    assert ttm.period_kind is PeriodKind.TTM
    assert ttm.fiscal_date == date(2025, 9, 30)
    assert ttm.revenue == pytest.approx(500.0)
    assert ttm.reported_eps == pytest.approx(14.0)
    assert ttm.delta_nwc == pytest.approx(26.0)
    assert ttm.nwc == pytest.approx(76.0)
    assert ttm.shares_outstanding == pytest.approx(20.0)
    assert ttm.availability_date == date(2025, 11, 14)


def test_three_quarters_produce_no_ttm() -> None:
    quarters = build_five_quarters()[:3]
    assert build_ttm_periods(quarters) == ()


# --- Task 7: stable FCFE and shared guards ---------------------------------


def stable_market() -> MarketInputs:
    return MarketInputs(
        as_of=date(2025, 12, 31),
        beta=1.1,
        risk_free_rate=0.07,
        equity_risk_premium=0.055,
    )


def stable_assumptions() -> ValuationAssumptions:
    return ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.06,
        stable_growth_rate=0.06,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=1.1,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.0,
        margin_of_safety=0.25,
    )


def test_stable_fcfe_matches_damodaran_fcfest(make_period) -> None:
    period = make_period(reported_eps=5.45, shares_outstanding=1.0)
    result = calculate_stable_fcfe(period, stable_market(), stable_assumptions())
    assert result.model is ModelName.STABLE_FCFE
    assert result.assumptions["cost_of_equity"] == pytest.approx(0.1305)
    assert result.value_per_share == pytest.approx(40.9716312056738, rel=1e-10, abs=1e-8)
    assert result.explicit_stage_pv == pytest.approx(0.0)
    assert result.terminal_value_share == pytest.approx(1.0)


def test_stable_fcfe_missing_eps_is_unavailable(make_period) -> None:
    period = make_period(reported_eps=None)
    with pytest.raises(DataUnavailableError):
        calculate_stable_fcfe(period, stable_market(), stable_assumptions())


def test_stable_fcfe_rejects_non_positive_eps_and_shares(make_period) -> None:
    with pytest.raises(ValuationMathError):
        calculate_stable_fcfe(make_period(reported_eps=0.0), stable_market(), stable_assumptions())
    with pytest.raises(ValuationMathError):
        calculate_stable_fcfe(
            make_period(shares_outstanding=0.0), stable_market(), stable_assumptions()
        )


def test_stable_fcfe_rejects_non_finite_beta(make_period) -> None:
    market = MarketInputs(
        as_of=date(2025, 12, 31),
        beta=float("inf"),
        risk_free_rate=0.07,
        equity_risk_premium=0.055,
    )
    with pytest.raises(ValuationMathError):
        calculate_stable_fcfe(make_period(), market, stable_assumptions())


def test_stable_fcfe_requires_cost_of_equity_above_growth(make_period) -> None:
    market = MarketInputs(
        as_of=date(2025, 12, 31),
        beta=0.0,
        risk_free_rate=0.05,
        equity_risk_premium=0.055,
    )
    with pytest.raises(ValuationMathError):
        calculate_stable_fcfe(make_period(), market, stable_assumptions())


def test_tax_rate_is_median_of_three_latest(make_period) -> None:
    periods = (
        make_period(
            fiscal_date=date(2023, 12, 31),
            availability_date=date(2024, 2, 1),
            pretax_income=100.0,
            tax_expense=25.0,
        ),
        make_period(
            fiscal_date=date(2024, 12, 31),
            availability_date=date(2025, 2, 1),
            pretax_income=200.0,
            tax_expense=60.0,
        ),
        make_period(
            fiscal_date=date(2025, 12, 31),
            availability_date=date(2026, 2, 1),
            pretax_income=400.0,
            tax_expense=80.0,
        ),
    )
    rate, dates = derive_effective_tax_rate(periods, date(2026, 6, 1))
    assert rate == pytest.approx(0.25)
    assert dates == (date(2025, 12, 31), date(2024, 12, 31), date(2023, 12, 31))


def test_tax_rate_ignores_future_periods_and_unusable_observations(make_period) -> None:
    periods = (
        make_period(
            fiscal_date=date(2026, 12, 31),
            availability_date=date(2027, 2, 1),
            pretax_income=100.0,
            tax_expense=50.0,
        ),
        make_period(
            fiscal_date=date(2025, 12, 31),
            availability_date=date(2026, 2, 1),
            pretax_income=100.0,
            tax_expense=25.0,
        ),
        make_period(
            fiscal_date=date(2024, 12, 31),
            availability_date=date(2025, 2, 1),
            pretax_income=0.0,
            tax_expense=10.0,
        ),
        make_period(
            fiscal_date=date(2023, 12, 31),
            availability_date=date(2024, 2, 1),
            pretax_income=80.0,
            tax_expense=20.0,
        ),
    )
    rate, dates = derive_effective_tax_rate(periods, date(2026, 6, 1))
    assert rate == pytest.approx(0.25)
    assert dates == (date(2025, 12, 31), date(2023, 12, 31))


def test_tax_rate_without_observations_is_unavailable(make_period) -> None:
    periods = (make_period(pretax_income=0.0, tax_expense=5.0),)
    with pytest.raises(DataUnavailableError):
        derive_effective_tax_rate(periods, date(2026, 6, 1))


# --- Task 8: two-stage FCFE -------------------------------------------------


def test_two_stage_fcfe_matches_damodaran_workbook(make_period) -> None:
    period = make_period(
        reported_eps=4.0,
        capex=3.7,
        depreciation=1.7,
        revenue=20.0,
        nwc=8.0,
        shares_outstanding=1.0,
    )
    market = MarketInputs(
        as_of=date(2025, 12, 31),
        beta=1.3,
        risk_free_rate=0.07,
        equity_risk_premium=0.055,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.227050969620234,
        stable_growth_rate=0.06,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=1.1,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.0992555831265509,
        margin_of_safety=0.25,
        capex_growth_rate=0.20,
        depreciation_growth_rate=0.20,
        revenue_growth_rate=0.18,
    )
    result = calculate_two_stage_fcfe(period, market, assumptions)
    assert [p.cash_flow for p in result.projections] == pytest.approx(
        [
            1.44934531768689,
            1.89792749443693,
            2.47104150291537,
            3.20127887639708,
            4.12945483295115,
        ],
        rel=1e-10,
        abs=1e-8,
    )
    assert result.assumptions["terminal_cash_flow"] == pytest.approx(5.89724099266713)
    assert result.explicit_stage_pv == pytest.approx(8.40368507114114)
    assert result.terminal_value_pv == pytest.approx(43.1598751388765)
    assert result.value_per_share == pytest.approx(51.5635602100177)


def test_two_stage_fcfe_zero_revenue_is_rejected(make_period) -> None:
    period = make_period(revenue=0.0)
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcfe(period, stable_market(), stable_assumptions())


def test_two_stage_fcfe_missing_fields_are_unavailable(make_period) -> None:
    for overrides in (
        {"capex": None},
        {"depreciation": None},
        {"nwc": None},
        {"shares_outstanding": None},
    ):
        period = make_period(**overrides)
        with pytest.raises(DataUnavailableError):
            calculate_two_stage_fcfe(period, stable_market(), stable_assumptions())


def test_two_stage_fcfe_requires_stable_ke_above_growth(make_period) -> None:
    market = MarketInputs(
        as_of=date(2025, 12, 31),
        beta=1.3,
        risk_free_rate=0.05,
        equity_risk_premium=0.0,
    )
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcfe(make_period(), market, stable_assumptions())


# --- Task 9: two-stage FCFF -------------------------------------------------


def fcff_market() -> MarketInputs:
    return MarketInputs(
        as_of=date(2025, 12, 31),
        beta=0.8,
        risk_free_rate=0.053,
        equity_risk_premium=0.055,
    )


def fcff_assumptions() -> ValuationAssumptions:
    return ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.105634325588301,
        stable_growth_rate=0.06,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=0.8,
        pretax_cost_of_debt=0.055,
        target_debt_weight=0.0144014503888806,
        margin_of_safety=0.25,
        equity_option_value=1500.0,
    )


def test_two_stage_fcff_matches_damodaran_workbook(make_period) -> None:
    period = make_period(
        ebit=5186.0,
        capex=2152.0,
        depreciation=1228.0,
        revenue=16701.0,
        nwc=3755.0,
        delta_nwc=499.0,
        cash=500.0,
        total_debt=1822.0,
        shares_outstanding=993.57,
    )
    result = calculate_two_stage_fcff(period, fcff_market(), fcff_assumptions(), tax_rate=0.2849)
    assert result.model is ModelName.TWO_STAGE_FCFF
    assert result.assumptions["high_stage_wacc"] == pytest.approx(0.0961694755567984)
    assert [p.cash_flow for p in result.projections] == pytest.approx(
        [
            2681.99139547176,
            2965.30174776604,
            3278.53939805712,
            3624.86569628555,
            4007.77593946084,
        ],
        rel=1e-10,
        abs=1e-8,
    )
    assert result.assumptions["terminal_cash_flow"] == pytest.approx(3247.37712358278)
    assert result.assumptions["enterprise_value"] == pytest.approx(69175.1523336163)
    assert result.value_per_share == pytest.approx(66.7825642215609)


def test_two_stage_fcff_rejects_invalid_tax(make_period) -> None:
    period = make_period()
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcff(period, fcff_market(), fcff_assumptions(), tax_rate=1.0)
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcff(period, fcff_market(), fcff_assumptions(), tax_rate=-0.1)


def test_two_stage_fcff_rejects_non_positive_revenue_or_shares(make_period) -> None:
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcff(
            make_period(revenue=0.0), fcff_market(), fcff_assumptions(), tax_rate=0.25
        )
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcff(
            make_period(shares_outstanding=0.0), fcff_market(), fcff_assumptions(), tax_rate=0.25
        )


def test_two_stage_fcff_missing_fields_are_unavailable(make_period) -> None:
    for overrides in (
        {"ebit": None},
        {"cash": None},
        {"total_debt": None},
    ):
        period = make_period(**overrides)
        with pytest.raises(DataUnavailableError):
            calculate_two_stage_fcff(period, fcff_market(), fcff_assumptions(), tax_rate=0.25)


def test_two_stage_fcff_requires_wacc_above_growth(make_period) -> None:
    period = make_period(
        ebit=5186.0,
        capex=2152.0,
        depreciation=1228.0,
        revenue=16701.0,
        nwc=3755.0,
        cash=500.0,
        total_debt=1822.0,
        shares_outstanding=993.57,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.105634325588301,
        stable_growth_rate=0.10,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=0.8,
        pretax_cost_of_debt=0.055,
        target_debt_weight=0.0144014503888806,
        margin_of_safety=0.25,
    )
    with pytest.raises(ValuationMathError):
        calculate_two_stage_fcff(period, fcff_market(), assumptions, tax_rate=0.2849)
