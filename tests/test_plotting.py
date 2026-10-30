from pathlib import Path

import numpy as np

from wavebird.models import ModelName, ModelResult, ShillerPoint
from wavebird.plotting import build_figure, filter_report_window, parse_window, plot_report

DCF_LABELS = {
    f"{label} ({suffix})"
    for label in ("Stable FCFE", "Two-stage FCFE", "Two-stage FCFF")
    for suffix in ("annual", "TTM")
}


def line_labels(figure) -> set[str]:
    return {line.get_label() for axis in figure.axes for line in axis.get_lines()}


def test_plot_has_six_named_panels_in_order(analysis_report) -> None:
    figure = build_figure(analysis_report)
    assert [axis.get_title() for axis in figure.axes] == [
        "Share price vs fair value per share",
        "Market capitalization vs intrinsic equity value",
        "Shiller PE (market)",
        "Operating performance",
        "Capital structure",
        "Debt and operating growth",
    ]


def test_plot_writes_png(tmp_path: Path, analysis_report) -> None:
    target = tmp_path / "report.png"
    assert plot_report(analysis_report, target) == target
    assert target.stat().st_size > 0


def test_all_six_dcf_series_are_labeled(analysis_report) -> None:
    figure = build_figure(analysis_report)
    labels = line_labels(figure)
    assert labels >= DCF_LABELS


def test_missing_model_point_is_nan_gap(analysis_report) -> None:
    first_annual = analysis_report.annual_history[0]
    unavailable = ModelResult(
        model=ModelName.STABLE_FCFE, unavailable_reason="missing reported EPS"
    )
    models = tuple(
        unavailable if result.model is ModelName.STABLE_FCFE else result
        for result in first_annual.models
    )
    modified_point = first_annual.model_copy(update={"models": models})
    history = (modified_point, *analysis_report.annual_history[1:])
    report = analysis_report.model_copy(update={"annual_history": history})

    figure = build_figure(report)
    stable_lines = [
        line
        for axis in figure.axes
        for line in axis.get_lines()
        if line.get_label() == "Stable FCFE (annual)"
    ]
    assert len(stable_lines) == 1
    ydata = np.asarray(stable_lines[0].get_ydata(), dtype=float)
    assert bool(np.isnan(ydata).any()), "expected a NaN gap"


def test_parse_window_accepts_documented_windows() -> None:
    from datetime import timedelta

    assert parse_window("52w") == timedelta(weeks=52)
    assert parse_window("3y") == timedelta(days=3 * 365)
    assert parse_window("5y") == timedelta(days=5 * 365)
    assert parse_window("10y") == timedelta(days=10 * 365)
    assert parse_window(None) is None
    assert parse_window("all") is None
    import pytest as _pytest

    with _pytest.raises(ValueError):
        parse_window("2q")


def test_filter_report_window_drops_old_points(analysis_report) -> None:
    from datetime import timedelta

    filtered = filter_report_window(analysis_report, timedelta(days=365))
    cutoff = analysis_report.as_of - timedelta(days=365)
    assert all(point.date >= cutoff for point in filtered.weekly_market_cap)
    assert all(point.period.availability_date >= cutoff for point in filtered.annual_history)
    assert len(filtered.weekly_market_cap) <= len(analysis_report.weekly_market_cap)
    assert filtered.symbol == analysis_report.symbol


def test_plot_report_accepts_window(tmp_path: Path, analysis_report) -> None:
    from datetime import timedelta

    path = plot_report(analysis_report, tmp_path / "w.png", window=timedelta(days=365))
    assert path.stat().st_size > 0


def test_price_panel_plots_price_and_fair_value(analysis_report) -> None:
    figure = build_figure(analysis_report)
    price_axis = figure.axes[0]
    labels = [str(line.get_label()) for line in price_axis.get_lines()]
    assert any("Share price" in label for label in labels)
    assert any("fair value" in label for label in labels)


def test_shiller_panel_plots_series(analysis_report) -> None:
    decorated = analysis_report.model_copy(
        update={
            "shiller_pe": (
                ShillerPoint(date=analysis_report.as_of, value=40.5),
                ShillerPoint(
                    date=analysis_report.as_of.replace(year=analysis_report.as_of.year - 1),
                    value=38.0,
                ),
            )
        }
    )
    figure = build_figure(decorated)
    shiller_axis = figure.axes[2]
    lines = shiller_axis.get_lines()
    assert len(lines) == 1
    assert "Shiller PE" in str(lines[0].get_label())


def test_window_filter_drops_old_shiller_points(analysis_report) -> None:
    old_year = analysis_report.as_of.year - 10
    decorated = analysis_report.model_copy(
        update={
            "shiller_pe": (
                ShillerPoint(date=analysis_report.as_of.replace(year=old_year), value=30.0),
                ShillerPoint(date=analysis_report.as_of, value=40.0),
            )
        }
    )
    filtered = filter_report_window(decorated, __import__("datetime").timedelta(days=365))
    assert len(filtered.shiller_pe) == 1
    assert filtered.shiller_pe[0].value == 40.0


def test_shiller_axis_has_independent_x_range(analysis_report) -> None:
    old = analysis_report.as_of.replace(year=analysis_report.as_of.year - 20)
    decorated = analysis_report.model_copy(
        update={
            "shiller_pe": (
                ShillerPoint(date=old, value=20.0),
                ShillerPoint(date=analysis_report.as_of, value=40.0),
            )
        }
    )
    figure = build_figure(decorated)
    price_axis = figure.axes[0]
    shiller_axis = figure.axes[2]
    assert price_axis.get_xlim() != shiller_axis.get_xlim()
