import re
from pathlib import Path

import numpy as np

from wavebird.models import ModelName, ModelResult, ShillerPoint
from wavebird.plotting import (
    MODEL_LABELS,
    build_figure,
    build_summary_figure,
    filter_report_window,
    parse_window,
    write_report_pdf,
)

DCF_LABELS = {
    f"{label} ({suffix})"
    for label in ("Stable FCFE", "Two-stage FCFE", "Two-stage FCFF")
    for suffix in ("annual", "TTM")
}


def line_labels(figure) -> set[str]:
    return {line.get_label() for axis in figure.axes for line in axis.get_lines()}


def test_plot_has_six_named_panels_in_order(analysis_report) -> None:
    figure = build_figure(analysis_report)
    titles = [axis.get_title() for axis in figure.axes]
    assert titles == [
        "",  # thesis summary header axis
        "Share price vs fair value per share",
        "Market capitalization vs intrinsic equity value",
        "Shiller PE (market)",
        "Operating performance",
        "Capital structure",
        "Debt and operating growth",
    ]


def test_report_pdf_has_summary_and_plot_pages(
    monkeypatch, tmp_path: Path, analysis_report
) -> None:
    from matplotlib.backends.backend_pdf import PdfPages

    pages: list[int] = []
    real_savefig = PdfPages.savefig

    def counting_savefig(self, figure, *args, **kwargs):
        pages.append(len(figure.axes))
        return real_savefig(self, figure, *args, **kwargs)

    monkeypatch.setattr(PdfPages, "savefig", counting_savefig)
    target = tmp_path / "report.pdf"
    assert write_report_pdf(analysis_report, target) == target
    assert target.read_bytes()[:4] == b"%PDF"
    # Page 1: the plain-English summary; page 2: the full figure.
    assert len(pages) == 2
    assert pages[0] == 1
    assert pages[1] == 7


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


def test_report_pdf_accepts_window(tmp_path: Path, analysis_report) -> None:
    from datetime import timedelta

    path = write_report_pdf(analysis_report, tmp_path / "w.pdf", window=timedelta(days=365))
    assert path.stat().st_size > 0


def test_price_panel_plots_price_and_fair_value(analysis_report) -> None:
    figure = build_figure(analysis_report)
    price_axis = figure.axes[1]
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
    shiller_axis = figure.axes[3]
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
    price_axis = figure.axes[1]
    shiller_axis = figure.axes[3]
    assert price_axis.get_xlim() != shiller_axis.get_xlim()


def _header_text(figure):
    header_axis = figure.axes[0]
    return "\n".join(text.get_text() for text in header_axis.texts)


def test_summary_header_shows_fair_value_price_and_mos(analysis_report) -> None:
    figure = build_figure(analysis_report)
    header = _header_text(figure)
    assert analysis_report.symbol in header
    assert str(analysis_report.screen_state.value) in header
    assert f"{analysis_report.current.market_price:,.2f}" in header
    assert f"{analysis_report.assumptions.margin_of_safety:.0%}" in header
    for model in ModelName:
        result = next(r for r in analysis_report.current.models if r.model is model)
        assert MODEL_LABELS[model] in header
        assert f"{result.value_per_share:,.2f}" in header
        assert f"{result.discount_to_value:+.1%}" in header


def test_summary_header_marks_unavailable_model(analysis_report) -> None:
    from wavebird.models import ModelResult

    broken = ModelResult(model=ModelName.STABLE_FCFE, unavailable_reason="negative EPS")
    points = analysis_report.current.models
    rebuilt_current = analysis_report.current.model_copy(
        update={"models": tuple(broken if r.model is ModelName.STABLE_FCFE else r for r in points)}
    )
    decorated = analysis_report.model_copy(update={"current": rebuilt_current})
    figure = build_figure(decorated)
    header = _header_text(figure)
    assert MODEL_LABELS[ModelName.STABLE_FCFE] in header
    assert "unavailable" in header
    assert "negative EPS" in header


def test_currency_axes_use_dollar_ticks_no_offset(analysis_report) -> None:
    from wavebird.models import MarketCapPoint

    scaled = analysis_report.model_copy(
        update={
            "weekly_market_cap": tuple(
                MarketCapPoint(
                    date=point.date,
                    market_price=point.market_price,
                    market_capitalization=point.market_capitalization * 1_000_000,
                )
                for point in analysis_report.weekly_market_cap
            )
        }
    )
    figure = build_figure(scaled)
    figure.canvas.draw()
    market_axis = figure.axes[2]  # summary, price, market
    assert market_axis.yaxis.get_offset_text().get_text() == ""
    labels = [t.get_text() for t in market_axis.get_yticklabels()]
    assert any(re.fullmatch(r"\$[\d.]+[TBM]", label) for label in labels if label)
    price_axis = figure.axes[1]
    price_labels = [t.get_text() for t in price_axis.get_yticklabels()]
    assert any(label.startswith("$") for label in price_labels if label)


def test_summary_figure_is_plain_english_table(analysis_report) -> None:
    figure = build_summary_figure(analysis_report)
    raw = "\n".join(text.get_text() for axis in figure.axes for text in axis.texts)
    text = " ".join(raw.split())
    assert "one share is worth" in text
    assert "pay at most" in text
    assert analysis_report.symbol in text
    assert MODEL_LABELS[ModelName.STABLE_FCFE] in text
    assert figure.axes[0].tables, "summary page must contain a table"


def test_summary_figure_caps_warning_wall(analysis_report) -> None:
    decorated = analysis_report.model_copy(
        update={"warnings": tuple(f"warning {index}" for index in range(1, 6))}
    )
    figure = build_summary_figure(decorated)
    raw = "\n".join(text.get_text() for axis in figure.axes for text in axis.texts)
    text = " ".join(raw.split())
    assert "warning 1; warning 2; warning 3" in text
    assert "+ 2 more data-completeness notices" in text
    assert "warning 4" not in text
