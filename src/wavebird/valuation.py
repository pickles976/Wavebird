import math
import statistics
from collections.abc import Sequence
from datetime import date, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from wavebird.config import ValuationAssumptions
from wavebird.errors import (
    DataUnavailableError,
    ProviderSchemaError,
    ValuationMathError,
)
from wavebird.models import (
    FinancialPeriod,
    MarketInputs,
    ModelName,
    ModelResult,
    PeriodKind,
    PricePoint,
    ProjectionPoint,
)

_FLOW_FIELDS = (
    "reported_eps",
    "revenue",
    "ebit",
    "pretax_income",
    "tax_expense",
    "interest_expense",
    "net_income",
    "capex",
    "depreciation",
    "dividends",
)

_BALANCE_FIELDS = (
    "current_assets",
    "cash",
    "current_liabilities",
    "current_debt",
    "total_debt",
    "book_equity",
    "shares_outstanding",
    "nwc",
)

_MIN_QUARTER_GAP_DAYS = 70
_MAX_QUARTER_GAP_DAYS = 110
_BETA_WINDOW_WEEKS = 104
_DEFAULT_BETA_OBSERVATIONS = 52

_ANNUAL_FALLBACK_DAYS = 90
_QUARTER_FALLBACK_DAYS = 45


def parse_number(value: Any, field: str, fiscal_date: str) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    try:
        number = float(text)
    except ValueError as error:
        raise ProviderSchemaError(
            f"field {field} for fiscal date {fiscal_date} is not numeric: {text!r}"
        ) from error
    if not math.isfinite(number):
        raise ProviderSchemaError(
            f"field {field} for fiscal date {fiscal_date} is not finite: {text!r}"
        )
    return number


def _nwc_from_balance(balance: dict[str, Any], fiscal: str) -> float | None:
    current_assets = parse_number(balance.get("totalCurrentAssets"), "totalCurrentAssets", fiscal)
    cash = parse_number(
        balance.get("cashAndShortTermInvestments"), "cashAndShortTermInvestments", fiscal
    )
    current_liabilities = parse_number(
        balance.get("totalCurrentLiabilities"), "totalCurrentLiabilities", fiscal
    )
    current_debt = parse_number(balance.get("currentDebt"), "currentDebt", fiscal)
    if (
        current_assets is None
        or cash is None
        or current_liabilities is None
        or current_debt is None
    ):
        return None
    return (current_assets - cash) - (current_liabilities - current_debt)


def _availability_date(
    is_annual: bool,
    fiscal_date: date,
    earnings_record: dict[str, Any] | None,
    quarterly_earnings: dict[str, dict[str, Any]],
    fiscal: str,
) -> date:
    reported_date: str | None = None
    if is_annual:
        q4 = quarterly_earnings.get(fiscal)
        if q4 is not None and q4.get("reportedDate"):
            reported_date = str(q4["reportedDate"])
        elif earnings_record is not None and earnings_record.get("reportedDate"):
            reported_date = str(earnings_record["reportedDate"])
        fallback = fiscal_date + timedelta(days=_ANNUAL_FALLBACK_DAYS)
    else:
        if earnings_record is not None and earnings_record.get("reportedDate"):
            reported_date = str(earnings_record["reportedDate"])
        fallback = fiscal_date + timedelta(days=_QUARTER_FALLBACK_DAYS)
    if reported_date:
        return date.fromisoformat(reported_date)
    return fallback


def parse_financial_periods(
    payloads: dict[str, dict[str, Any]],
    period_kind: PeriodKind,
) -> tuple[FinancialPeriod, ...]:
    is_annual = period_kind is PeriodKind.ANNUAL
    reports_key = "annualReports" if is_annual else "quarterlyReports"
    earnings_key = "annualEarnings" if is_annual else "quarterlyEarnings"

    def index(records: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        return {str(record["fiscalDateEnding"]): record for record in (records or [])}

    income = index(payloads["INCOME_STATEMENT"].get(reports_key))
    cash = index(payloads["CASH_FLOW"].get(reports_key))
    balance = index(payloads["BALANCE_SHEET"].get(reports_key))
    earnings = index(payloads["EARNINGS"].get(earnings_key))
    quarterly_earnings = index(payloads["EARNINGS"].get("quarterlyEarnings"))

    periods: list[FinancialPeriod] = []
    for fiscal in sorted(set(income) & set(cash) & set(balance)):
        income_record = income[fiscal]
        cash_record = cash[fiscal]
        balance_record = balance[fiscal]
        currency = income_record.get("reportedCurrency")
        if currency != "USD":
            continue

        earnings_record = earnings.get(fiscal)
        reported_eps = (
            parse_number(earnings_record.get("reportedEPS"), "reportedEPS", fiscal)
            if earnings_record is not None
            else None
        )
        fiscal_date = date.fromisoformat(fiscal)

        optional_numbers = {
            "revenue": parse_number(income_record.get("totalRevenue"), "totalRevenue", fiscal),
            "ebit": parse_number(income_record.get("ebit"), "ebit", fiscal),
            "pretax_income": parse_number(
                income_record.get("incomeBeforeTax"), "incomeBeforeTax", fiscal
            ),
            "tax_expense": parse_number(
                income_record.get("incomeTaxExpense"), "incomeTaxExpense", fiscal
            ),
            "interest_expense": parse_number(
                income_record.get("interestExpense"), "interestExpense", fiscal
            ),
            "net_income": parse_number(income_record.get("netIncome"), "netIncome", fiscal),
            "capex": parse_number(
                cash_record.get("capitalExpenditures"), "capitalExpenditures", fiscal
            ),
            "depreciation": parse_number(
                cash_record.get("depreciationDepletionAndAmortization"),
                "depreciationDepletionAndAmortization",
                fiscal,
            ),
            "dividends": parse_number(
                cash_record.get("dividendPayoutCommonStock"), "dividendPayoutCommonStock", fiscal
            ),
            "current_assets": parse_number(
                balance_record.get("totalCurrentAssets"), "totalCurrentAssets", fiscal
            ),
            "cash": parse_number(
                balance_record.get("cashAndShortTermInvestments"),
                "cashAndShortTermInvestments",
                fiscal,
            ),
            "current_liabilities": parse_number(
                balance_record.get("totalCurrentLiabilities"),
                "totalCurrentLiabilities",
                fiscal,
            ),
            "current_debt": parse_number(balance_record.get("currentDebt"), "currentDebt", fiscal),
            "total_debt": parse_number(
                balance_record.get("shortLongTermDebtTotal"), "shortLongTermDebtTotal", fiscal
            ),
            "book_equity": parse_number(
                balance_record.get("totalShareholderEquity"), "totalShareholderEquity", fiscal
            ),
            "shares_outstanding": parse_number(
                balance_record.get("commonStockSharesOutstanding"),
                "commonStockSharesOutstanding",
                fiscal,
            ),
        }
        capex = optional_numbers["capex"]
        if capex is not None:
            optional_numbers["capex"] = abs(capex)

        periods.append(
            FinancialPeriod(
                symbol=str(payloads["INCOME_STATEMENT"].get("symbol", "")),
                fiscal_date=fiscal_date,
                availability_date=_availability_date(
                    is_annual, fiscal_date, earnings_record, quarterly_earnings, fiscal
                ),
                period_kind=period_kind,
                reported_currency=str(currency),
                reported_eps=reported_eps,
                nwc=_nwc_from_balance(balance_record, fiscal),
                delta_nwc=None,
                **optional_numbers,
            )
        )
    return tuple(periods)


def parse_weekly_prices(payload: dict[str, Any]) -> tuple[PricePoint, ...]:
    series = payload.get("Weekly Adjusted Time Series")
    if not isinstance(series, dict):
        raise ProviderSchemaError(
            "Alpha Vantage weekly response is missing 'Weekly Adjusted Time Series'"
        )
    points: list[PricePoint] = []
    for day, values in series.items():
        close = parse_number(values.get("4. close"), "4. close", str(day))
        adjusted = parse_number(values.get("5. adjusted close"), "5. adjusted close", str(day))
        if close is None or adjusted is None or close <= 0 or adjusted <= 0:
            raise ProviderSchemaError(
                f"Alpha Vantage weekly prices for {day} are missing or non-positive"
            )
        points.append(
            PricePoint(date=date.fromisoformat(str(day)), close=close, adjusted_close=adjusted)
        )
    points.sort(key=lambda point: point.date)
    return tuple(points)


def build_ttm_periods(quarters: tuple[FinancialPeriod, ...]) -> tuple[FinancialPeriod, ...]:
    ordered = sorted(quarters, key=lambda period: period.fiscal_date)
    ttms: list[FinancialPeriod] = []
    for end in range(3, len(ordered)):
        window = ordered[end - 3 : end + 1]
        prior = ordered[end - 4] if end >= 4 else None
        if prior is None:
            continue
        gaps_ok = all(
            _MIN_QUARTER_GAP_DAYS
            <= (later.fiscal_date - earlier.fiscal_date).days
            <= _MAX_QUARTER_GAP_DAYS
            for earlier, later in pairwise(window)
        )
        if not gaps_ok:
            continue

        def summed(field: str, items: Sequence[FinancialPeriod]) -> float | None:
            values = [getattr(period, field) for period in items]
            if any(value is None for value in values):
                return None
            return sum(value for value in values if value is not None)

        flow = {field: summed(field, window) for field in _FLOW_FIELDS}
        last = window[-1]
        balance = {field: getattr(last, field) for field in _BALANCE_FIELDS}
        delta_nwc = None
        if last.nwc is not None and prior.nwc is not None:
            delta_nwc = last.nwc - prior.nwc
        ttms.append(
            FinancialPeriod(
                symbol=last.symbol,
                fiscal_date=last.fiscal_date,
                availability_date=max(period.availability_date for period in window),
                period_kind=PeriodKind.TTM,
                reported_currency=last.reported_currency,
                delta_nwc=delta_nwc,
                **flow,
                **balance,
            )
        )
    return tuple(ttms)


def calculate_beta(
    stock: tuple[PricePoint, ...],
    spy: tuple[PricePoint, ...],
    as_of: date,
    min_observations: int = _DEFAULT_BETA_OBSERVATIONS,
) -> float:
    stock_map = {point.date: point for point in stock if point.date <= as_of}
    spy_map = {point.date: point for point in spy if point.date <= as_of}
    aligned = sorted(set(stock_map) & set(spy_map))[-_BETA_WINDOW_WEEKS:]
    if len(aligned) < min_observations + 1:
        raise ValuationMathError(
            f"beta requires {min_observations + 1} aligned weekly observations; "
            f"found {len(aligned)}"
        )
    stock_returns = [
        stock_map[later].adjusted_close / stock_map[earlier].adjusted_close - 1
        for earlier, later in pairwise(aligned)
    ]
    spy_returns = [
        spy_map[later].adjusted_close / spy_map[earlier].adjusted_close - 1
        for earlier, later in pairwise(aligned)
    ]
    variance = statistics.variance(spy_returns)
    if variance == 0 or not math.isfinite(variance):
        raise ValuationMathError("benchmark return variance is zero or non-finite")
    beta = statistics.covariance(stock_returns, spy_returns) / variance
    if not math.isfinite(beta):
        raise ValuationMathError("beta is non-finite")
    return beta


# --- Task 7: shared guards, stable FCFE, effective tax rate -----------------


def cost_of_equity(beta: float, risk_free_rate: float, erp: float) -> float:
    value = risk_free_rate + beta * erp
    if not math.isfinite(value):
        raise ValuationMathError("cost of equity is not finite")
    return value


def require_fields(
    period: FinancialPeriod,
    model: ModelName,
    *names: str,
) -> tuple[float, ...]:
    missing = [name for name in names if getattr(period, name) is None]
    if missing:
        fields = ", ".join(missing)
        raise DataUnavailableError(
            f"{model.value} unavailable for {period.symbol} {period.fiscal_date}: missing {fields}"
        )
    return tuple(float(getattr(period, name)) for name in names)


def valid_result(
    *,
    model: ModelName,
    value_per_share: float,
    shares: float,
    explicit_stage_pv: float,
    terminal_value_pv: float,
    projections: tuple[ProjectionPoint, ...] = (),
    assumptions: dict[str, float | int | str],
) -> ModelResult:
    values = (
        value_per_share,
        shares,
        explicit_stage_pv,
        terminal_value_pv,
        *(point.cash_flow for point in projections),
        *(point.present_value for point in projections),
    )
    if shares <= 0 or not all(math.isfinite(value) for value in values):
        raise ValuationMathError("valuation result is non-finite or shares are non-positive")
    if explicit_stage_pv + terminal_value_pv == 0:
        raise ValuationMathError("valuation result has zero total present value")
    return ModelResult(
        model=model,
        value_per_share=value_per_share,
        intrinsic_equity_value=value_per_share * shares,
        explicit_stage_pv=explicit_stage_pv,
        terminal_value_pv=terminal_value_pv,
        terminal_value_share=terminal_value_pv / (explicit_stage_pv + terminal_value_pv),
        projections=projections,
        assumptions=assumptions,
    )


def calculate_stable_fcfe(
    period: FinancialPeriod,
    market: MarketInputs,
    assumptions: ValuationAssumptions,
) -> ModelResult:
    (eps, shares) = require_fields(
        period, ModelName.STABLE_FCFE, "reported_eps", "shares_outstanding"
    )
    if eps <= 0 or shares <= 0:
        raise ValuationMathError("stable FCFE requires positive EPS and shares")
    ke = cost_of_equity(market.beta, market.risk_free_rate, market.equity_risk_premium)
    growth = assumptions.stable_growth_rate
    if ke <= growth:
        raise ValuationMathError("stable FCFE requires cost of equity > stable growth")
    fcfe_next = eps * (1 + growth) * (1 - growth / assumptions.stable_roe)
    value = fcfe_next / (ke - growth)
    return valid_result(
        model=ModelName.STABLE_FCFE,
        value_per_share=value,
        shares=shares,
        explicit_stage_pv=0.0,
        terminal_value_pv=value * shares,
        assumptions={
            "cost_of_equity": ke,
            "stable_growth_rate": growth,
            "stable_roe": assumptions.stable_roe,
        },
    )


def derive_effective_tax_rate(
    periods: tuple[FinancialPeriod, ...] | list[FinancialPeriod],
    as_of: date,
) -> tuple[float, tuple[date, ...]]:
    candidates: list[tuple[date, float]] = []
    for period in sorted(periods, key=lambda item: item.fiscal_date, reverse=True):
        if period.availability_date > as_of:
            continue
        if period.pretax_income is None or period.tax_expense is None:
            continue
        if period.pretax_income <= 0 or period.tax_expense < 0:
            continue
        rate = period.tax_expense / period.pretax_income
        if not math.isfinite(rate) or rate < 0 or rate >= 1:
            continue
        candidates.append((period.fiscal_date, rate))
        if len(candidates) == 3:
            break
    if not candidates:
        raise DataUnavailableError(
            "no eligible pretax income and tax expense observations "
            f"available by {as_of.isoformat()}"
        )
    dates = tuple(fiscal for fiscal, _ in candidates)
    return median(rate for _, rate in candidates), dates


def calculate_two_stage_fcfe(
    period: FinancialPeriod,
    market: MarketInputs,
    assumptions: ValuationAssumptions,
) -> ModelResult:
    eps0, capex0, dep0, revenue0, nwc0, shares = require_fields(
        period,
        ModelName.TWO_STAGE_FCFE,
        "reported_eps",
        "capex",
        "depreciation",
        "revenue",
        "nwc",
        "shares_outstanding",
    )
    if eps0 <= 0 or shares <= 0:
        raise ValuationMathError("two-stage FCFE requires positive EPS and shares")
    if revenue0 <= 0:
        raise ValuationMathError("two-stage FCFE requires positive revenue")

    high_ke = cost_of_equity(market.beta, market.risk_free_rate, market.equity_risk_premium)
    stable_growth = assumptions.stable_growth_rate
    terminal_ke = cost_of_equity(
        assumptions.stable_beta, market.risk_free_rate, market.equity_risk_premium
    )
    if terminal_ke <= stable_growth:
        raise ValuationMathError("stable cost of equity must exceed stable growth")

    capex_growth = assumptions.capex_growth_rate
    dep_growth = assumptions.depreciation_growth_rate
    revenue_growth = assumptions.revenue_growth_rate
    if capex_growth is None or dep_growth is None or revenue_growth is None:
        raise ValuationMathError("projection growth rates must be resolved defaults")

    debt_ratio = assumptions.target_debt_weight
    nwc_ratio = nwc0 / revenue0
    projections: list[ProjectionPoint] = []
    explicit_pv = 0.0
    for year in range(1, assumptions.forecast_years + 1):
        eps = eps0 * (1 + assumptions.high_growth_rate) ** year
        capex_ps = capex0 / shares * (1 + capex_growth) ** year
        dep_ps = dep0 / shares * (1 + dep_growth) ** year
        revenue_ps = revenue0 / shares * (1 + revenue_growth) ** year
        previous_revenue_ps = revenue0 / shares * (1 + revenue_growth) ** (year - 1)
        delta_nwc_ps = nwc_ratio * (revenue_ps - previous_revenue_ps)
        fcfe_ps = eps - (capex_ps - dep_ps) * (1 - debt_ratio) - delta_nwc_ps * (1 - debt_ratio)
        present_value = fcfe_ps / (1 + high_ke) ** year
        projections.append(
            ProjectionPoint(year=year, cash_flow=fcfe_ps, present_value=present_value)
        )
        explicit_pv += present_value

    final_eps = eps0 * (1 + assumptions.high_growth_rate) ** assumptions.forecast_years
    terminal_eps = final_eps * (1 + stable_growth)
    terminal_fcfe = terminal_eps * (1 - stable_growth / assumptions.stable_roe)
    terminal_value = terminal_fcfe / (terminal_ke - stable_growth)
    terminal_pv_per_share = terminal_value / (1 + high_ke) ** assumptions.forecast_years
    value_per_share = explicit_pv + terminal_pv_per_share

    return valid_result(
        model=ModelName.TWO_STAGE_FCFE,
        value_per_share=value_per_share,
        shares=shares,
        explicit_stage_pv=explicit_pv * shares,
        terminal_value_pv=terminal_pv_per_share * shares,
        projections=tuple(projections),
        assumptions={
            "high_stage_cost_of_equity": high_ke,
            "stable_cost_of_equity": terminal_ke,
            "terminal_cash_flow": terminal_fcfe,
            "high_growth_rate": assumptions.high_growth_rate,
            "stable_growth_rate": stable_growth,
            "capex_growth_rate": capex_growth,
            "depreciation_growth_rate": dep_growth,
            "revenue_growth_rate": revenue_growth,
            "target_debt_weight": debt_ratio,
        },
    )


def wacc(ke: float, kd: float, tax_rate: float, debt_weight: float) -> float:
    return ke * (1 - debt_weight) + kd * (1 - tax_rate) * debt_weight


def calculate_two_stage_fcff(
    period: FinancialPeriod,
    market: MarketInputs,
    assumptions: ValuationAssumptions,
    tax_rate: float,
) -> ModelResult:
    ebit0, capex0, dep0, revenue0, nwc0, cash, total_debt, shares = require_fields(
        period,
        ModelName.TWO_STAGE_FCFF,
        "ebit",
        "capex",
        "depreciation",
        "revenue",
        "nwc",
        "cash",
        "total_debt",
        "shares_outstanding",
    )
    if not (0 <= tax_rate < 1) or not math.isfinite(tax_rate):
        raise ValuationMathError("tax rate must be within [0, 1)")
    if revenue0 <= 0 or shares <= 0:
        raise ValuationMathError("two-stage FCFF requires positive revenue and shares")

    capex_growth = assumptions.capex_growth_rate
    dep_growth = assumptions.depreciation_growth_rate
    revenue_growth = assumptions.revenue_growth_rate
    if capex_growth is None or dep_growth is None or revenue_growth is None:
        raise ValuationMathError("projection growth rates must be resolved defaults")

    high_ke = cost_of_equity(market.beta, market.risk_free_rate, market.equity_risk_premium)
    high_wacc = wacc(
        high_ke, assumptions.pretax_cost_of_debt, tax_rate, assumptions.target_debt_weight
    )
    stable_ke = cost_of_equity(
        assumptions.stable_beta, market.risk_free_rate, market.equity_risk_premium
    )
    stable_wacc = wacc(
        stable_ke, assumptions.pretax_cost_of_debt, tax_rate, assumptions.target_debt_weight
    )
    stable_growth = assumptions.stable_growth_rate
    if high_wacc <= stable_growth or stable_wacc <= stable_growth:
        raise ValuationMathError("WACC must exceed stable growth in both stages")

    nwc_ratio = nwc0 / revenue0
    projections: list[ProjectionPoint] = []
    explicit_pv = 0.0
    ebit = ebit0
    nopat = 0.0
    for year in range(1, assumptions.forecast_years + 1):
        ebit = ebit0 * (1 + assumptions.high_growth_rate) ** year
        capex = capex0 * (1 + capex_growth) ** year
        depreciation = dep0 * (1 + dep_growth) ** year
        revenue = revenue0 * (1 + revenue_growth) ** year
        previous_revenue = revenue0 * (1 + revenue_growth) ** (year - 1)
        delta_nwc = nwc_ratio * (revenue - previous_revenue)
        nopat = ebit * (1 - tax_rate)
        fcff = nopat - (capex - depreciation) - delta_nwc
        present_value = fcff / (1 + high_wacc) ** year
        projections.append(ProjectionPoint(year=year, cash_flow=fcff, present_value=present_value))
        explicit_pv += present_value

    terminal_nopat = nopat * (1 + stable_growth)
    terminal_fcff = terminal_nopat * (1 - stable_growth / assumptions.stable_roc)
    terminal_value = terminal_fcff / (stable_wacc - stable_growth)
    terminal_pv = terminal_value / (1 + high_wacc) ** assumptions.forecast_years
    enterprise_value = explicit_pv + terminal_pv
    equity_value = enterprise_value + cash - total_debt - assumptions.equity_option_value
    value_per_share = equity_value / shares

    return valid_result(
        model=ModelName.TWO_STAGE_FCFF,
        value_per_share=value_per_share,
        shares=shares,
        explicit_stage_pv=explicit_pv,
        terminal_value_pv=terminal_pv,
        projections=tuple(projections),
        assumptions={
            "high_stage_wacc": high_wacc,
            "stable_wacc": stable_wacc,
            "tax_rate": tax_rate,
            "terminal_cash_flow": terminal_fcff,
            "enterprise_value": enterprise_value,
            "equity_value": equity_value,
            "cash": cash,
            "total_debt": total_debt,
            "equity_option_value": assumptions.equity_option_value,
            "high_growth_rate": assumptions.high_growth_rate,
            "stable_growth_rate": stable_growth,
            "capex_growth_rate": capex_growth,
            "depreciation_growth_rate": dep_growth,
            "revenue_growth_rate": revenue_growth,
            "target_debt_weight": assumptions.target_debt_weight,
        },
    )
