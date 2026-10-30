from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from pydantic import ValidationError

from wavebird.alpha_vantage import AlphaDataset, ProviderPayload
from wavebird.config import ValuationAssumptions
from wavebird.errors import (
    DataUnavailableError,
    ProviderSchemaError,
    QuotaError,
    TransportError,
    ValuationMathError,
)
from wavebird.models import (
    AnalysisPoint,
    AnalysisReport,
    MarketCapPoint,
    MarketInputs,
    ModelName,
    ModelResult,
    PeriodKind,
    PricePoint,
    RatePoint,
    ScreenState,
    SensitivityPoint,
    ShillerPoint,
    SourceMetadata,
)
from wavebird.shiller import ShillerClient
from wavebird.valuation import (
    build_ttm_periods,
    calculate_beta,
    calculate_stable_fcfe,
    calculate_two_stage_fcfe,
    calculate_two_stage_fcff,
    derive_effective_tax_rate,
    parse_financial_periods,
    parse_weekly_prices,
)


class AlphaProvider(Protocol):
    def get(self, dataset: AlphaDataset, symbol: str, refresh: bool = False) -> ProviderPayload: ...


class RatesProvider(Protocol):
    def get_rates(self, as_of: date, refresh: bool = False) -> RatePoint: ...


SPY_SYMBOL = "SPY"

DISCOUNT_RATE_DELTAS = (-0.01, 0.0, 0.01)
TERMINAL_GROWTH_DELTAS = (-0.005, 0.0, 0.005)

_TTM_PRIOR_MIN_DAYS = 330
_TTM_PRIOR_MAX_DAYS = 400


@dataclass
class _Bundle:
    symbol: str
    statements: dict[str, dict]
    company_prices: tuple[PricePoint, ...]
    spy_prices: tuple[PricePoint, ...]
    sources: list[SourceMetadata] = field(default_factory=list)


class AnalysisService:
    def __init__(
        self,
        alpha: AlphaProvider,
        rates: RatesProvider,
        shiller: ShillerClient | None = None,
    ) -> None:
        self.alpha = alpha
        self.rates = rates
        self.shiller = shiller

    def analyze(
        self,
        symbol: str,
        assumptions: ValuationAssumptions,
        as_of: date,
        refresh: bool = False,
    ) -> AnalysisReport:
        normalized = symbol.strip().upper()
        bundle = self._fetch_bundle(normalized, refresh)
        warnings: list[str] = []
        for source in bundle.sources:
            if source.stale:
                warnings.append(
                    f"STALE source: {source.provider} {source.dataset} {source.symbol} "
                    f"fetched {source.fetched_at.isoformat()}"
                )

        annuals = parse_financial_periods(bundle.statements, PeriodKind.ANNUAL)
        quarters = parse_financial_periods(bundle.statements, PeriodKind.QUARTER)
        ttms = build_ttm_periods(quarters)

        current_ttm = self._latest_available(ttms, as_of)
        if current_ttm is None:
            raise DataUnavailableError(
                f"no TTM period available by {as_of.isoformat()} for {normalized}"
            )

        eligible_annuals = [p for p in annuals if p.availability_date <= as_of]
        eligible_ttms = [p for p in ttms if p.availability_date <= as_of]

        rate_dates = sorted(
            {p.availability_date for p in (*eligible_annuals, *eligible_ttms)} | {as_of}
        )
        rates_by_date = self._resolve_rates(rate_dates, refresh)
        for rate_point in rates_by_date.values():
            bundle.sources.append(rate_point.source)

        shiller_pe: tuple[ShillerPoint, ...] = ()
        if self.shiller is not None:
            try:
                fetch = self.shiller.get_series(refresh=refresh)
                shiller_pe = fetch.records
                if fetch.source is not None:
                    bundle.sources.append(fetch.source)
            except (TransportError, QuotaError, ProviderSchemaError) as error:
                warnings.append(f"Shiller PE unavailable: {error}")

        annual_points = self._build_points(
            eligible_annuals, bundle, rates_by_date, assumptions, as_of, warnings, current=False
        )
        ttm_points = self._build_points(
            eligible_ttms, bundle, rates_by_date, assumptions, as_of, warnings, current=False
        )
        current_point = self._build_point(
            period=current_ttm,
            valuation_date=as_of,
            bundle=bundle,
            rates_by_date=rates_by_date,
            assumptions=assumptions,
            kind_periods=eligible_ttms,
            warnings=warnings,
            is_current=True,
        )
        if current_point is None:
            raise DataUnavailableError(
                f"current market inputs unavailable for {normalized} at {as_of.isoformat()}"
            )

        weekly_market_cap = self._weekly_market_cap(bundle.company_prices, [*annuals, *quarters])

        valid = [r for r in current_point.models if r.unavailable_reason is None]
        if len(valid) != 3:
            screen = ScreenState.INCONCLUSIVE
        elif all(
            r.value_per_share is not None
            and r.value_per_share > 0
            and r.discount_to_value is not None
            and r.discount_to_value >= assumptions.margin_of_safety
            for r in valid
        ):
            screen = ScreenState.PASSES
        else:
            screen = ScreenState.DOES_NOT_PASS

        return AnalysisReport(
            symbol=normalized,
            as_of=as_of,
            assumptions=assumptions,
            current=current_point,
            annual_history=tuple(annual_points),
            ttm_history=tuple(ttm_points),
            weekly_market_cap=tuple(weekly_market_cap),
            shiller_pe=shiller_pe,
            sources=tuple(bundle.sources),
            warnings=tuple(warnings),
            screen_state=screen,
        )

    # --- fetching --------------------------------------------------------

    def _fetch_bundle(self, symbol: str, refresh: bool) -> _Bundle:
        sources: list[SourceMetadata] = []
        statements: dict[str, dict] = {}
        for dataset in (
            AlphaDataset.INCOME_STATEMENT,
            AlphaDataset.CASH_FLOW,
            AlphaDataset.BALANCE_SHEET,
            AlphaDataset.EARNINGS,
        ):
            payload = self.alpha.get(dataset, symbol, refresh=refresh)
            statements[dataset.value] = payload.data
            sources.append(payload.source)

        company_weekly = self.alpha.get(AlphaDataset.WEEKLY_ADJUSTED, symbol, refresh=refresh)
        spy_weekly = self.alpha.get(AlphaDataset.WEEKLY_ADJUSTED, SPY_SYMBOL, refresh=refresh)
        sources.append(company_weekly.source)
        sources.append(spy_weekly.source)
        company_prices = parse_weekly_prices(company_weekly.data)
        spy_prices = parse_weekly_prices(spy_weekly.data)
        return _Bundle(
            symbol=symbol,
            statements=statements,
            company_prices=company_prices,
            spy_prices=spy_prices,
            sources=sources,
        )

    def _resolve_rates(
        self,
        dates: list[date],
        refresh: bool,
    ) -> dict[date, RatePoint]:
        resolved: dict[date, RatePoint] = {}
        first = True
        for day in dates:
            resolved[day] = self.rates.get_rates(day, refresh=refresh and first)
            first = False
        return resolved

    # --- point construction ----------------------------------------------

    def _latest_available(self, periods, as_of: date):
        available = [p for p in periods if p.availability_date <= as_of]
        if not available:
            return None
        return max(available, key=lambda p: (p.fiscal_date, p.availability_date))

    def _build_points(
        self,
        periods,
        bundle: _Bundle,
        rates_by_date: dict[date, RatePoint],
        assumptions: ValuationAssumptions,
        as_of: date,
        warnings: list[str],
        current: bool,
    ) -> list[AnalysisPoint]:
        points: list[AnalysisPoint] = []
        ordered = sorted(periods, key=lambda p: p.fiscal_date)
        for period in ordered:
            prior = self._find_prior(period, ordered)
            point = self._build_point(
                period=period,
                valuation_date=period.availability_date,
                bundle=bundle,
                rates_by_date=rates_by_date,
                assumptions=assumptions,
                kind_periods=ordered,
                warnings=warnings,
                is_current=False,
                prior=prior,
            )
            if point is not None:
                points.append(point)
        return points

    def _find_prior(self, period, ordered):
        same_kind = [p for p in ordered if p.fiscal_date < period.fiscal_date]
        if not same_kind:
            return None
        if period.period_kind is PeriodKind.ANNUAL:
            return max(same_kind, key=lambda p: p.fiscal_date)
        gap = (period.fiscal_date - same_kind[-1].fiscal_date).days
        if _TTM_PRIOR_MIN_DAYS <= gap <= _TTM_PRIOR_MAX_DAYS:
            return same_kind[-1]
        return None

    def _build_point(
        self,
        period,
        valuation_date: date,
        bundle: _Bundle,
        rates_by_date: dict[date, RatePoint],
        assumptions: ValuationAssumptions,
        kind_periods,
        warnings: list[str],
        is_current: bool,
        prior=None,
    ) -> AnalysisPoint | None:
        label = f"{period.period_kind.value} {period.fiscal_date.isoformat()}"
        close = _latest_close(bundle.company_prices, valuation_date)
        if close is None:
            warnings.append(f"omitted {label}: no company close at or before the valuation date")
            return None
        try:
            beta = calculate_beta(bundle.company_prices, bundle.spy_prices, valuation_date)
        except ValuationMathError as error:
            warnings.append(f"omitted {label}: beta unavailable ({error})")
            return None
        rate = rates_by_date.get(valuation_date)
        if rate is None:
            warnings.append(f"omitted {label}: no rate observation at or before the date")
            return None

        market = MarketInputs(
            as_of=valuation_date,
            beta=beta,
            risk_free_rate=rate.risk_free_rate,
            equity_risk_premium=rate.equity_risk_premium,
        )
        try:
            tax_rate, tax_dates = derive_effective_tax_rate(kind_periods, valuation_date)
        except DataUnavailableError:
            tax_rate, tax_dates = None, ()

        models = self._run_models(
            period, market, assumptions, tax_rate, tax_dates, market_price=close.close
        )
        diagnostics = self._diagnostics(period, prior, assumptions, tax_rate)
        sensitivity = ()
        if is_current:
            sensitivity = self._sensitivity_all(period, market, assumptions, tax_rate)
        return AnalysisPoint(
            period=period,
            market=market,
            market_price=close.close,
            market_capitalization=close.close * (period.shares_outstanding or 0.0),
            models=tuple(models),
            diagnostics=diagnostics,
            sensitivity=sensitivity,
        )

    def _run_models(
        self,
        period,
        market: MarketInputs,
        assumptions: ValuationAssumptions,
        tax_rate: float | None,
        tax_dates: tuple[date, ...],
        market_price: float,
    ) -> list[ModelResult]:
        tax_note = (
            {"tax_rate_source_periods": ",".join(d.isoformat() for d in tax_dates)}
            if tax_dates
            else {}
        )
        results: list[ModelResult] = []
        for runner, name in (
            (calculate_stable_fcfe, ModelName.STABLE_FCFE),
            (calculate_two_stage_fcfe, ModelName.TWO_STAGE_FCFE),
        ):
            try:
                result = runner(period, market, assumptions)
                if tax_note:
                    result = result.model_copy(
                        update={"assumptions": {**result.assumptions, **tax_note}}
                    )
            except (DataUnavailableError, ValuationMathError) as error:
                result = ModelResult(model=name, unavailable_reason=str(error))
            results.append(self._with_discount(result, market_price))

        if tax_rate is None:
            results.append(
                ModelResult(
                    model=ModelName.TWO_STAGE_FCFF,
                    unavailable_reason="no effective tax rate available from eligible periods",
                )
            )
        else:
            try:
                result = calculate_two_stage_fcff(period, market, assumptions, tax_rate=tax_rate)
            except (DataUnavailableError, ValuationMathError) as error:
                result = ModelResult(model=ModelName.TWO_STAGE_FCFF, unavailable_reason=str(error))
            results.append(self._with_discount(result, market_price))
        return results

    @staticmethod
    def _with_discount(result: ModelResult, market_price: float) -> ModelResult:
        if result.value_per_share is None:
            return result
        if result.value_per_share <= 0:
            return result
        return result.model_copy(
            update={"discount_to_value": 1 - market_price / result.value_per_share}
        )

    # --- sensitivity -------------------------------------------------------

    def sensitivity_for(
        self,
        model: ModelName,
        period,
        market: MarketInputs,
        assumptions: ValuationAssumptions,
        tax_rate: float | None,
    ) -> tuple[SensitivityPoint, ...]:
        points: list[SensitivityPoint] = []
        for discount_delta in DISCOUNT_RATE_DELTAS:
            for growth_delta in TERMINAL_GROWTH_DELTAS:
                points.append(
                    self._sensitivity_cell(
                        model, period, market, assumptions, tax_rate, discount_delta, growth_delta
                    )
                )
        return tuple(points)

    def _sensitivity_cell(
        self,
        model: ModelName,
        period,
        market: MarketInputs,
        assumptions: ValuationAssumptions,
        tax_rate: float | None,
        discount_delta: float,
        growth_delta: float,
    ) -> SensitivityPoint:
        try:
            shifted_market = MarketInputs.model_validate(
                {**market.model_dump(), "risk_free_rate": market.risk_free_rate + discount_delta}
            )
            shifted_assumptions = ValuationAssumptions.model_validate(
                {
                    **assumptions.model_dump(),
                    "stable_growth_rate": assumptions.stable_growth_rate + growth_delta,
                }
            )
            if model is ModelName.STABLE_FCFE:
                result = calculate_stable_fcfe(period, shifted_market, shifted_assumptions)
            elif model is ModelName.TWO_STAGE_FCFE:
                result = calculate_two_stage_fcfe(period, shifted_market, shifted_assumptions)
            else:
                if tax_rate is None:
                    raise DataUnavailableError("no effective tax rate available")
                result = calculate_two_stage_fcff(
                    period, shifted_market, shifted_assumptions, tax_rate=tax_rate
                )
            return SensitivityPoint(
                model=model,
                discount_rate_delta=discount_delta,
                terminal_growth_delta=growth_delta,
                value_per_share=result.value_per_share,
            )
        except (ValidationError, DataUnavailableError, ValuationMathError) as error:
            return SensitivityPoint(
                model=model,
                discount_rate_delta=discount_delta,
                terminal_growth_delta=growth_delta,
                unavailable_reason=str(error),
            )

    def _sensitivity_all(self, period, market, assumptions, tax_rate):
        grid: list[SensitivityPoint] = []
        for model in ModelName:
            grid.extend(self.sensitivity_for(model, period, market, assumptions, tax_rate))
        return tuple(grid)

    # --- diagnostics -------------------------------------------------------

    def _diagnostics(self, period, prior, assumptions, tax_rate):
        d: dict[str, float | str | None] = {}

        def put(name: str, value: float | str | None, reason: str | None = None) -> None:
            d[name] = value
            if reason is not None:
                d[f"{name}_reason"] = reason

        def ratio(
            numerator: float | None, denominator: float | None
        ) -> tuple[float | None, str | None]:
            if numerator is None:
                return None, "input missing"
            if denominator is None or denominator <= 0:
                return None, "denominator missing or non-positive"
            return numerator / denominator, None

        def difference(
            current: float | None, earlier: float | None
        ) -> tuple[float | None, str | None]:
            if current is None or earlier is None:
                return None, "prior or current value missing"
            return current - earlier, None

        def growth(current: float | None, earlier: float | None) -> tuple[float | None, str | None]:
            if current is None or earlier is None:
                return None, "prior or current value missing"
            if earlier <= 0:
                return None, "prior value non-positive"
            return current / earlier - 1, None

        put("eps", period.reported_eps)
        put("revenue", period.revenue)
        put("ebit", period.ebit)
        put("gross_debt", period.total_debt)
        put("cash", period.cash)

        nopat: float | None = None
        if period.ebit is not None:
            if tax_rate is None:
                put("nopat", None, "effective tax rate unavailable")
            else:
                nopat = period.ebit * (1 - tax_rate)
                put("nopat", nopat)
        else:
            put("nopat", None, "EBIT missing")

        capex_minus_dep: float | None = None
        if period.capex is not None and period.depreciation is not None:
            capex_minus_dep = period.capex - period.depreciation

        fcff: float | None = None
        if nopat is not None and capex_minus_dep is not None and period.delta_nwc is not None:
            fcff = nopat - capex_minus_dep - period.delta_nwc
        put(
            "actual_fcff",
            fcff,
            None if fcff is not None else "NOPAT, CapEx, depreciation, or delta NWC missing",
        )

        fcfe: float | None = None
        if (
            period.net_income is not None
            and capex_minus_dep is not None
            and period.delta_nwc is not None
        ):
            retain = 1 - assumptions.target_debt_weight
            fcfe = period.net_income - capex_minus_dep * retain - period.delta_nwc * retain
        put(
            "actual_fcfe",
            fcfe,
            None if fcfe is not None else "net income, CapEx, depreciation, or delta NWC missing",
        )

        net_debt: float | None = None
        if period.total_debt is not None and period.cash is not None:
            net_debt = period.total_debt - period.cash
        put("net_debt", net_debt, None if net_debt is not None else "gross debt or cash missing")

        d2c, d2c_reason = ratio(period.total_debt, None)
        if period.total_debt is not None and period.book_equity is not None:
            d2c, d2c_reason = ratio(period.total_debt, period.total_debt + period.book_equity)
        elif d2c_reason is None:
            d2c, d2c_reason = None, "book equity missing"
        put("debt_to_capital", d2c, d2c_reason)

        margin, margin_reason = ratio(period.ebit, period.revenue)
        put("operating_margin", margin, margin_reason)

        roe: float | None = None
        roc: float | None = None
        prior_net_debt: float | None = None
        if prior is None:
            put("roe", None, "no prior same-kind period")
            put("roc", None, "no prior same-kind period")
        else:
            if (
                period.net_income is not None
                and period.book_equity is not None
                and prior.book_equity is not None
            ):
                roe, roe_reason = ratio(
                    period.net_income, (period.book_equity + prior.book_equity) / 2
                )
                put("roe", roe, roe_reason)
            else:
                put("roe", None, "net income or book equity missing")
            invested = (
                None
                if period.book_equity is None or period.total_debt is None or period.cash is None
                else period.book_equity + period.total_debt - period.cash
            )
            prior_invested = (
                None
                if prior.book_equity is None or prior.total_debt is None or prior.cash is None
                else prior.book_equity + prior.total_debt - prior.cash
            )
            if nopat is not None and invested is not None and prior_invested is not None:
                roc, roc_reason = ratio(nopat, (invested + prior_invested) / 2)
                put("roc", roc, roc_reason)
            else:
                put("roc", None, "NOPAT or invested capital missing")
            if prior.total_debt is not None and prior.cash is not None:
                prior_net_debt = prior.total_debt - prior.cash

        rev_change, rev_change_reason = difference(period.revenue, prior.revenue if prior else None)
        put("revenue_change", rev_change, rev_change_reason)
        rev_growth, rev_growth_reason = growth(period.revenue, prior.revenue if prior else None)
        put("revenue_growth", rev_growth, rev_growth_reason)
        ebit_change, ebit_change_reason = difference(period.ebit, prior.ebit if prior else None)
        put("ebit_change", ebit_change, ebit_change_reason)
        ebit_growth, ebit_growth_reason = growth(period.ebit, prior.ebit if prior else None)
        put("ebit_growth", ebit_growth, ebit_growth_reason)
        fcff_change, fcff_change_reason = None, None
        prior_fcff: float | None = None
        if prior is not None and tax_rate is not None and prior.ebit is not None:
            prior_nopat = prior.ebit * (1 - tax_rate)
            if (
                prior.capex is not None
                and prior.depreciation is not None
                and prior.delta_nwc is not None
            ):
                prior_fcff = prior_nopat - (prior.capex - prior.depreciation) - prior.delta_nwc
        fcff_change, fcff_change_reason = difference(fcff, prior_fcff)
        put("fcff_change", fcff_change, fcff_change_reason)
        fcff_growth, fcff_growth_reason = growth(fcff, prior_fcff)
        put("fcff_growth", fcff_growth, fcff_growth_reason)
        nd_change, nd_change_reason = difference(net_debt, prior_net_debt)
        put("net_debt_change", nd_change, nd_change_reason)
        nd_growth, nd_growth_reason = growth(net_debt, prior_net_debt)
        put("net_debt_growth", nd_growth, nd_growth_reason)

        indicator: str | None = "false"
        indicator_reason: str | None = None
        if prior is None or prior.revenue is None or prior.ebit is None or prior_net_debt is None:
            indicator, indicator_reason = None, "prior period or its net debt missing"
        elif nd_growth is None or rev_growth is None or ebit_growth is None:
            indicator, indicator_reason = None, "growth rates unavailable"
        else:
            d2c_increased = (
                d2c is not None
                and prior.total_debt is not None
                and prior.book_equity is not None
                and (prior.total_debt / (prior.total_debt + prior.book_equity)) is not None
                and prior.book_equity + prior.total_debt > 0
                and d2c > prior.total_debt / (prior.total_debt + prior.book_equity)
            )
            if prior.revenue > 0 and prior.ebit > 0 and prior_net_debt > 0:
                indicator = (
                    "true"
                    if (nd_growth > rev_growth and nd_growth > ebit_growth and d2c_increased)
                    else "false"
                )
            else:
                indicator, indicator_reason = (
                    None,
                    "prior revenue, EBIT, or net debt not positive",
                )
        put("financing_assisted", indicator, indicator_reason)
        return d

    # --- market capitalization --------------------------------------------

    def _weekly_market_cap(
        self,
        prices: tuple[PricePoint, ...],
        periods,
    ) -> list[MarketCapPoint]:
        shares_observations = sorted(
            (
                (p.availability_date, p.shares_outstanding)
                for p in periods
                if p.shares_outstanding is not None
            ),
            key=lambda item: item[0],
        )
        if not shares_observations:
            return []
        points: list[MarketCapPoint] = []
        for price in prices:
            prior = [(a, s) for a, s in shares_observations if a <= price.date]
            if not prior:
                continue
            shares = max(prior, key=lambda item: item[0])[1]
            if shares is None or shares <= 0:
                continue
            # Alpha restates historical share counts to the modern share basis, so the
            # series must pair split-adjusted closes with them; raw closes would distort
            # every pre-split week by the cumulative split factor.
            adjusted = price.adjusted_close
            points.append(
                MarketCapPoint(
                    date=price.date,
                    market_price=adjusted,
                    market_capitalization=adjusted * shares,
                )
            )
        return points


def _latest_close(prices: tuple[PricePoint, ...], as_of: date) -> PricePoint | None:
    candidates = [p for p in prices if p.date <= as_of]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.date)
