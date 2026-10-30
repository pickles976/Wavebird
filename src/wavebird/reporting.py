from wavebird.models import AnalysisPoint, AnalysisReport, ModelName

MODEL_LABELS = {
    ModelName.STABLE_FCFE: "Stable FCFE",
    ModelName.TWO_STAGE_FCFE: "Two-stage FCFE",
    ModelName.TWO_STAGE_FCFF: "Two-stage FCFF",
}

TRADEOFFS = {
    ModelName.STABLE_FCFE: (
        "Stable FCFE is a steady-state sanity check for mature firms; it is compact but "
        "highly sensitive to stable growth and ROE and cannot represent a transition period."
    ),
    ModelName.TWO_STAGE_FCFE: (
        "Two-stage FCFE models the explicit transition in equity cash flow; it exposes "
        "CapEx, depreciation, working-capital, and target-debt assumptions but is "
        "sensitive to all four."
    ),
    ModelName.TWO_STAGE_FCFF: (
        "Two-stage FCFF is the operating/capital-structure cross-check and is preferable "
        "when borrowing distorts equity cash flow; it adds WACC, target-debt, tax, "
        "cash/debt, and option-value assumptions."
    ),
}

NON_CAUSAL_SENTENCE = "This shows co-movement, not that debt caused growth."


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5"}


def format_money(value: float | None, currency: str = "USD", decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    code = currency.upper()
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} ")
    return f"{symbol}{value:,.{decimals}f}"


def format_money_short(value: float | None, currency: str = "USD") -> str:
    """Abbreviate large amounts for chart axes: $561M, $200.5B, $1.5T."""
    if value is None:
        return "N/A"
    code = currency.upper()
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} ")
    for factor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= factor:
            scaled = value / factor
            digits = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{symbol}{digits}{suffix}"
    return f"{symbol}{value:,.0f}"


def format_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def format_optional(value: float | str | None) -> str:
    return "N/A" if value is None else str(value)


def _comparison_row(point: AnalysisPoint, label: str, currency: str = "USD") -> list[str]:
    shares = point.period.shares_outstanding
    cells = [label]
    for model in ModelName:
        result = next(r for r in point.models if r.model is model)
        if result.unavailable_reason is not None:
            cells.append(f"{MODEL_LABELS[model]}: N/A ({result.unavailable_reason})")
            continue
        value = result.value_per_share
        discount = result.discount_to_value
        explicit_ps = (
            result.explicit_stage_pv / shares
            if result.explicit_stage_pv is not None and shares
            else None
        )
        terminal_ps = (
            result.terminal_value_pv / shares
            if result.terminal_value_pv is not None and shares
            else None
        )
        terminal_share = (
            result.terminal_value_share if result.terminal_value_share is not None else None
        )
        cells.append(
            f"{MODEL_LABELS[model]}: value/share {format_money(value, currency)}, "
            f"discount {format_rate(discount)}, "
            f"explicit PV/share {format_money(explicit_ps, currency)}, "
            f"terminal PV/share {format_money(terminal_ps, currency)}, "
            f"terminal share {format_rate(terminal_share)}"
        )
    return cells


def render_text(report: AnalysisReport) -> str:
    lines: list[str] = []
    current = report.current

    lines.append(f"Wavebird analysis for {report.symbol} (as of {report.as_of.isoformat()})")
    lines.append(f"Screen state: {report.screen_state.value}")
    currency = report.assumptions.currency
    lines.append(f"Market price: {format_money(current.market_price, currency)}")
    lines.append(f"Market capitalization: {format_money(current.market_capitalization, currency)}")
    lines.append("")

    lines.append("Model comparison")
    for row in _comparison_row(current, report.symbol, currency):
        lines.append(f"  {row}")
    lines.append("")

    lines.append("Model tradeoffs and availability")
    for model in ModelName:
        result = next(r for r in current.models if r.model is model)
        lines.append(f"  {MODEL_LABELS[model]}: {TRADEOFFS[model]}")
        if result.unavailable_reason is not None:
            lines.append(f"    Unavailable: {result.unavailable_reason}")
    lines.append("")

    lines.append("Assumptions (supplied and resolved defaults)")
    a = report.assumptions
    lines.append(f"  forecast years: {a.forecast_years}")
    lines.append(f"  high growth rate: {format_rate(a.high_growth_rate)}")
    lines.append(f"  stable growth rate: {format_rate(a.stable_growth_rate)}")
    lines.append(f"  stable ROE: {format_rate(a.stable_roe)}")
    lines.append(f"  stable ROC: {format_rate(a.stable_roc)}")
    lines.append(f"  stable beta: {a.stable_beta}")
    lines.append(f"  pre-tax cost of debt: {format_rate(a.pretax_cost_of_debt)}")
    lines.append(f"  target debt weight: {format_rate(a.target_debt_weight)}")
    lines.append(f"  margin of safety: {format_rate(a.margin_of_safety)}")
    lines.append(f"  capex growth rate: {format_rate(a.capex_growth_rate)}")
    lines.append(f"  depreciation growth rate: {format_rate(a.depreciation_growth_rate)}")
    lines.append(f"  revenue growth rate: {format_rate(a.revenue_growth_rate)}")
    lines.append(f"  currency: {a.currency}")
    lines.append(f"  equity option value: {format_money(a.equity_option_value, currency)}")
    lines.append("")

    lines.append("Sensitivity grids (discount-rate delta vs terminal-growth delta)")
    for model in ModelName:
        lines.append(f"  Sensitivity - {MODEL_LABELS[model]}")
        cells = [c for c in current.sensitivity if c.model is model]
        grid = {(c.discount_rate_delta, c.terminal_growth_delta): c for c in cells}
        header = "      growth\\rate " + " ".join(
            f"{delta:>+8.3f}" for delta in sorted({c[0] for c in grid})
        )
        lines.append(header)
        for growth_delta in sorted({c[1] for c in grid}):
            row = f"      {growth_delta:>+11.3f} "
            for rate_delta in sorted({c[0] for c in grid}):
                cell = grid.get((rate_delta, growth_delta))
                value = (
                    format_money(cell.value_per_share, currency)
                    if cell is not None and cell.value_per_share is not None
                    else "N/A"
                )
                row += f"{value:>12}"
            lines.append(row)
    lines.append("")

    lines.append("Diagnostics (annual and TTM history plus current)")
    points = [(*report.annual_history, *report.ttm_history)]
    flat = [p for group in points for p in group]
    for point in flat:
        d = point.diagnostics
        lines.append(
            f"  {point.period.period_kind.value} {point.period.fiscal_date.isoformat()}: "
            f"EPS {format_optional(_num(d.get('eps')))}, "
            f"revenue {format_money(_num(d.get('revenue')), currency)}, "
            f"EBIT {format_money(_num(d.get('ebit')), currency)}, "
            f"FCFE {format_money(_num(d.get('actual_fcfe')), currency)}, "
            f"FCFF {format_money(_num(d.get('actual_fcff')), currency)}, "
            f"gross debt {format_money(_num(d.get('gross_debt')), currency)}, "
            f"net debt {format_money(_num(d.get('net_debt')), currency)}, "
            f"leverage {format_rate(_num(d.get('debt_to_capital')))}, "
            f"ROE {format_rate(_num(d.get('roe')))}, "
            f"ROC {format_rate(_num(d.get('roc')))}, "
            f"operating margin {format_rate(_num(d.get('operating_margin')))}"
        )
    lines.append(f"  current {current.period.fiscal_date.isoformat()}: see JSON for full detail")
    lines.append("")

    indicator = current.diagnostics.get("financing_assisted")
    indicator_reason = current.diagnostics.get("financing_assisted_reason")
    lines.append("Financing-assisted growth indicator")
    lines.append(f"  {format_optional(indicator)}")
    if indicator_reason is not None:
        lines.append(f"  Reason: {indicator_reason}")
    lines.append(f"  {NON_CAUSAL_SENTENCE}")
    lines.append("")

    lines.append("Data provenance")
    for source in report.sources:
        state = "STALE" if source.stale else "FRESH"
        lines.append(
            f"  {source.provider} {source.dataset} {source.symbol}: {state}, "
            f"fetched {source.fetched_at.isoformat()}, "
            f"expires {source.expires_at.isoformat()}, {source.source_url}"
        )
    lines.append("")

    if report.warnings:
        lines.append("Warnings")
        for warning in report.warnings:
            lines.append(f"  - {warning}")
        lines.append("")

    lines.append(
        "Historical reconstruction uses earnings-release or proxy dates; "
        "it is not a look-ahead-free backtest and provider history may include restatements."
    )
    lines.append("Not investment advice.")
    return "\n".join(lines)


def _num(value: float | str | None) -> float | None:
    if isinstance(value, float):
        return value
    return None


def render_json(report: AnalysisReport) -> str:
    return report.model_dump_json(indent=2)
