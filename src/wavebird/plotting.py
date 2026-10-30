import math
from datetime import timedelta
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from wavebird.models import AnalysisPoint, AnalysisReport, ModelName

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


def build_figure(report: AnalysisReport, window_days: timedelta | None = None) -> Figure:
    report = filter_report_window(report, window_days)
    figure, axes = plt.subplots(6, 1, figsize=(14, 24), constrained_layout=True)
    price_axis, market_axis, shiller_axis, operating_axis, debt_axis, growth_axis = axes
    # The Shiller PE series spans ~150 years of market history; linking its x-axis to
    # the company series (a few years) would compress every company panel. Link only
    # the company panels to each other.
    for company_axis in (market_axis, operating_axis, debt_axis, growth_axis):
        company_axis.sharex(price_axis)
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


def plot_report(report: AnalysisReport, destination: Path, window: timedelta | None = None) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure = build_figure(report, window_days=window)
    try:
        figure.savefig(destination, dpi=150)
    finally:
        plt.close(figure)
    return destination
