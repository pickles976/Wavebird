import math
from datetime import timedelta
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import Bbox

from wavebird.models import AnalysisPoint, AnalysisReport, ModelName, ScreenState
from wavebird.reporting import format_money, format_money_short, format_rate

MODEL_LABELS = {
    ModelName.STABLE_FCFE: "Stable FCFE",
    ModelName.TWO_STAGE_FCFE: "Two-stage FCFE",
    ModelName.TWO_STAGE_FCFF: "Two-stage FCFF",
}

FOOTER_TEXT = (
    "Earnings-release/proxy reconstruction; not a look-ahead-free backtest. "
    "Co-movement is not causality."
)


def _model_value(point: AnalysisPoint, model: ModelName, field: str) -> float:
    result = next(r for r in point.models if r.model is model)
    value = getattr(result, field)
    return math.nan if value is None else value


def _diag(point: AnalysisPoint, name: str) -> float:
    value = point.diagnostics.get(name)
    return float(value) if isinstance(value, float) else math.nan


def _series(points, value_of):
    dates = [point.period.availability_date for point in points]
    values = [value_of(point) for point in points]
    return dates, values


def parse_window(value: str | None) -> timedelta | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in ("", "all"):
        return None
    table = {"52w": timedelta(weeks=52)}
    for years in (3, 5, 10):
        table[f"{years}y"] = timedelta(days=365 * years)
    if text not in table:
        raise ValueError(f"unknown window {value!r}; use 52w, 3y, 5y, 10y, or all")
    return table[text]


def filter_report_window(report: AnalysisReport, window_days: timedelta | None) -> AnalysisReport:
    if window_days is None:
        return report
    cutoff = report.as_of - window_days
    return report.model_copy(
        update={
            "shiller_pe": tuple(point for point in report.shiller_pe if point.date >= cutoff),
            "weekly_market_cap": tuple(
                point for point in report.weekly_market_cap if point.date >= cutoff
            ),
            "annual_history": tuple(
                point for point in report.annual_history if point.period.availability_date >= cutoff
            ),
            "ttm_history": tuple(
                point for point in report.ttm_history if point.period.availability_date >= cutoff
            ),
        }
    )


def _summary_text(report: AnalysisReport) -> str:
    price = report.current.market_price
    mos = report.assumptions.margin_of_safety
    lines = [
        f"{report.symbol} — as of {report.as_of.isoformat()} — {report.screen_state.value}",
        (
            f"Current price {format_money(price, report.assumptions.currency)}"
            f"  ·  margin of safety {mos:.0%}"
            "  ·  buy below = fair value x (1 - MOS)"
        ),
    ]
    for result in report.current.models:
        label = MODEL_LABELS[result.model]
        if result.unavailable_reason is not None:
            lines.append(f"{label:<18} unavailable — {result.unavailable_reason}")
            continue
        fair = result.value_per_share
        if fair is None:  # the model validator forbids valid results without a value
            lines.append(f"{label:<18} unavailable")
            continue
        threshold = fair * (1 - mos)
        discount = result.discount_to_value
        verdict = "PASS" if discount is not None and discount >= mos else "FAIL"
        money = format_money(fair, report.assumptions.currency)
        below = format_money(threshold, report.assumptions.currency)
        lines.append(
            f"{label:<18} fair {money}  ·  buy below {below}"
            f"  ·  discount {discount:+.1%} vs MOS {mos:.0%} → {verdict}"
        )
    return "\n".join(lines)


def build_figure(report: AnalysisReport, window_days: timedelta | None = None) -> Figure:
    report = filter_report_window(report, window_days)
    figure, axes = plt.subplots(
        7,
        1,
        figsize=(14, 26),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [0.18, 1, 1, 1, 1, 1, 1]},
    )
    (
        summary_axis,
        price_axis,
        market_axis,
        shiller_axis,
        operating_axis,
        debt_axis,
        growth_axis,
    ) = axes
    summary_axis.axis("off")
    summary_axis.text(
        0.01,
        0.98,
        _summary_text(report),
        transform=summary_axis.transAxes,
        ha="left",
        va="top",
        family="monospace",
        fontsize=11,
        verticalalignment="top",
        parse_math=False,
    )
    # The Shiller PE series spans ~150 years of market history; linking its x-axis to
    # the company series (a few years) would compress every company panel. Link only
    # the company panels to each other.
    for company_axis in (market_axis, operating_axis, debt_axis, growth_axis):
        company_axis.sharex(price_axis)
    currency = report.assumptions.currency
    for value_axis in (price_axis, market_axis, operating_axis, debt_axis):
        _apply_currency_axis(value_axis, currency)
    styles = {
        ModelName.STABLE_FCFE: ("o", "-"),
        ModelName.TWO_STAGE_FCFE: ("s", "--"),
        ModelName.TWO_STAGE_FCFF: ("^", ":"),
    }

    price_axis.plot(
        [point.date for point in report.weekly_market_cap],
        [point.market_price for point in report.weekly_market_cap],
        label="Share price (split-adjusted)",
        linestyle="-",
        color="0.3",
    )
    for kind_points, suffix in (
        (report.annual_history, "annual"),
        (report.ttm_history, "TTM"),
    ):
        for model in ModelName:
            marker, linestyle = styles[model]
            dates, values = _series(
                kind_points, lambda p, m=model: _model_value(p, m, "value_per_share")
            )
            price_axis.plot(
                dates,
                values,
                marker=marker,
                linestyle=linestyle,
                label=f"{MODEL_LABELS[model]} fair value ({suffix})",
            )
    price_axis.set_title("Share price vs fair value per share")
    price_axis.set_ylabel("Currency units (per share)")
    price_axis.grid(True)
    price_axis.legend()

    market_axis.plot(
        [point.date for point in report.weekly_market_cap],
        [point.market_capitalization for point in report.weekly_market_cap],
        label="Weekly market capitalization",
        linestyle="-",
        color="0.3",
    )
    for kind_points, suffix in (
        (report.annual_history, "annual"),
        (report.ttm_history, "TTM"),
    ):
        for model in ModelName:
            marker, linestyle = styles[model]
            dates, values = _series(
                kind_points, lambda p, m=model: _model_value(p, m, "intrinsic_equity_value")
            )
            market_axis.plot(
                dates,
                values,
                marker=marker,
                linestyle=linestyle,
                label=f"{MODEL_LABELS[model]} ({suffix})",
            )
    market_axis.set_title("Market capitalization vs intrinsic equity value")
    market_axis.set_ylabel("Currency units")
    market_axis.grid(True)
    market_axis.legend()

    if report.shiller_pe:
        shiller_axis.plot(
            [point.date for point in report.shiller_pe],
            [point.value for point in report.shiller_pe],
            label="Shiller PE (monthly)",
            linestyle="-",
            color="tab:blue",
        )
        shiller_axis.legend()
    else:
        shiller_axis.text(
            0.5,
            0.5,
            "Shiller PE unavailable",
            transform=shiller_axis.transAxes,
            ha="center",
            va="center",
        )
    shiller_axis.set_title("Shiller PE (market)")
    shiller_axis.set_ylabel("Cyclically adjusted P/E")
    shiller_axis.grid(True)

    for kind_points, suffix in (
        (report.annual_history, "annual"),
        (report.ttm_history, "TTM"),
    ):
        revenue_dates, revenue = _series(kind_points, lambda p: _diag(p, "revenue"))
        ebit_dates, ebit = _series(kind_points, lambda p: _diag(p, "ebit"))
        fcff_dates, fcff = _series(kind_points, lambda p: _diag(p, "actual_fcff"))
        operating_axis.plot(
            revenue_dates, revenue, marker="o", linestyle="-", label=f"Revenue ({suffix})"
        )
        operating_axis.plot(ebit_dates, ebit, marker="s", linestyle="--", label=f"EBIT ({suffix})")
        operating_axis.plot(fcff_dates, fcff, marker="^", linestyle=":", label=f"FCFF ({suffix})")
    operating_axis.set_title("Operating performance")
    operating_axis.set_ylabel("Currency units")
    operating_axis.grid(True)
    operating_axis.legend()

    for kind_points, suffix in (
        (report.annual_history, "annual"),
        (report.ttm_history, "TTM"),
    ):
        debt_dates, gross = _series(kind_points, lambda p: _diag(p, "gross_debt"))
        cash_dates, cash = _series(kind_points, lambda p: _diag(p, "cash"))
        net_dates, net = _series(kind_points, lambda p: _diag(p, "net_debt"))
        debt_axis.plot(debt_dates, gross, marker="o", linestyle="-", label=f"Gross debt ({suffix})")
        debt_axis.plot(cash_dates, cash, marker="s", linestyle="--", label=f"Cash ({suffix})")
        debt_axis.plot(net_dates, net, marker="^", linestyle=":", label=f"Net debt ({suffix})")
    debt_axis.set_title("Capital structure")
    debt_axis.set_ylabel("Currency units")
    debt_axis.grid(True)
    debt_axis.legend()

    for name, label in (
        ("revenue_growth", "Revenue growth"),
        ("ebit_growth", "EBIT growth"),
        ("fcff_growth", "FCFF growth"),
        ("net_debt_growth", "Net-debt growth"),
        ("debt_to_capital", "Debt-to-capital"),
    ):
        for kind_points, suffix in (
            (report.annual_history, "annual"),
            (report.ttm_history, "TTM"),
        ):
            dates, values = _series(kind_points, lambda p, n=name: _diag(p, n))
            growth_axis.plot(dates, values, marker=".", linestyle="-", label=f"{label} ({suffix})")
    growth_axis.set_title("Debt and operating growth")
    growth_axis.set_ylabel("Rate")
    growth_axis.grid(True)
    growth_axis.legend(ncol=2, fontsize=8)

    figure.text(
        0.01,
        0.005,
        FOOTER_TEXT,
        fontsize=9,
        ha="left",
        va="bottom",
    )
    return figure


def _apply_currency_axis(axis, currency: str) -> None:
    axis.yaxis.set_major_formatter(
        FuncFormatter(lambda value, _position: format_money_short(value, currency))
    )


def _plain_english_lines(report: AnalysisReport) -> str:
    assumptions = report.assumptions
    currency = assumptions.currency
    mos = assumptions.margin_of_safety
    lines = [
        (
            f"{report.symbol} last traded at "
            f"{format_money(report.current.market_price, currency)} on {report.as_of.isoformat()}."
        )
    ]
    screen_sentence = {
        ScreenState.PASSES: "The screen passes: every model clears your margin of safety.",
        ScreenState.DOES_NOT_PASS: (
            "The screen does not pass: at least one model's discount to value"
            " is below your margin of safety."
        ),
        ScreenState.INCONCLUSIVE: (
            "The screen is inconclusive: some models could not produce a value."
        ),
    }[report.screen_state]
    lines.append(screen_sentence)
    lines.append("")
    for result in report.current.models:
        label = MODEL_LABELS[result.model]
        if result.unavailable_reason is not None:
            lines.append(f"{label} could not produce a value: {result.unavailable_reason}.")
            continue
        fair = result.value_per_share
        discount = result.discount_to_value
        if fair is None:
            lines.append(f"{label} could not produce a value.")
            continue
        threshold = fair * (1 - mos)
        if discount is not None and discount >= 0:
            gap = f"the price is {discount:.1%} below fair value"
        elif discount is not None:
            gap = f"the price is {abs(discount):.1%} above fair value"
        else:
            gap = "no discount is available"
        verdict = "passes" if discount is not None and discount >= mos else "fails"
        lines.append(
            f"{label} says one share is worth {format_money(fair, currency)}. "
            f"With your {mos:.0%} margin of safety you would want to pay at most "
            f"{format_money(threshold, currency)}; today {gap} - this model {verdict} your test."
        )
    lines.append("")
    lines.append(
        f"These figures assume the business grows "
        f"{assumptions.high_growth_rate:.1%} a year for {assumptions.forecast_years} years "
        f"and {assumptions.stable_growth_rate:.1%} forever afterwards. The stable stage "
        f"assumes a {assumptions.stable_roe:.1%} return on equity and a "
        f"{assumptions.stable_roc:.1%} return on capital. Discount rates use the "
        f"Damodaran risk-free rate and equity risk premium with the stock's measured beta."
    )
    if report.warnings:
        shown = report.warnings[:3]
        lines.append(f"Warnings: {'; '.join(shown)}")
        hidden = len(report.warnings) - len(shown)
        if hidden > 0:
            lines.append(
                f"+ {hidden} more data-completeness notices; see the text report for the full list."
            )
    lines.append("Not investment advice.")
    import textwrap

    return "\n".join(textwrap.fill(line, width=100, subsequent_indent="    ") for line in lines)


def build_summary_figure(report: AnalysisReport) -> Figure:
    figure, axis = plt.subplots(figsize=(14, 10))
    axis.axis("off")
    figure.suptitle(f"Wavebird valuation summary - {report.symbol}", fontsize=14)
    axis.text(
        0.02,
        0.60,
        _plain_english_lines(report),
        transform=axis.transAxes,
        ha="left",
        va="top",
        family="monospace",
        fontsize=11,
        parse_math=False,
    )
    currency = report.assumptions.currency
    mos = report.assumptions.margin_of_safety
    rows = []
    for result in report.current.models:
        label = MODEL_LABELS[result.model]
        if result.unavailable_reason is not None or result.value_per_share is None:
            rows.append([label, "N/A", "N/A", "N/A", "unavailable"])
            continue
        discount = result.discount_to_value
        verdict = "PASS" if discount is not None and discount >= mos else "FAIL"
        rows.append(
            [
                label,
                format_money(result.value_per_share, currency),
                format_money(result.value_per_share * (1 - mos), currency),
                format_rate(discount),
                verdict,
            ]
        )
    table = axis.table(
        cellText=rows,
        colLabels=["Model", "Fair value/share", "Buy below (MOS)", "Discount", "Verdict"],
        loc="upper center",
        bbox=Bbox.from_extents(0.08, 0.64, 0.84, 0.92),
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    for cell in table.get_celld().values():
        cell.get_text().set_parse_math(False)
    return figure


def write_report_pdf(
    report: AnalysisReport, destination: Path, window: timedelta | None = None
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(destination) as pdf:
        summary = build_summary_figure(report)
        pdf.savefig(summary)
        plt.close(summary)
        figure = build_figure(report, window_days=window)
        pdf.savefig(figure)
        plt.close(figure)
    return destination
