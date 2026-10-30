from datetime import UTC, date, datetime

from wavebird.models import (
    AnalysisReport,
    ModelName,
    ModelResult,
    ScreenState,
    SensitivityPoint,
    SourceMetadata,
)
from wavebird.reporting import render_json, render_text

MODEL_LABELS = ("Stable FCFE", "Two-stage FCFE", "Two-stage FCFF")


def test_text_report_keeps_models_separate(analysis_report) -> None:
    text = render_text(analysis_report)
    for label in MODEL_LABELS:
        assert label in text
    assert "PASSES SCREEN" in text
    assert "average fair value" not in text.lower()
    assert "not investment advice" in text.lower()


def test_json_round_trips_domain_model(analysis_report) -> None:
    rendered = render_json(analysis_report)
    assert '"screen_state": "PASSES SCREEN"' in rendered
    assert '"terminal_value_share"' in rendered


def test_provenance_lists_sources_and_timestamps(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "alpha_vantage" in text
    assert "damodaran" in text
    assert "fake://INCOME_STATEMENT/ACME" in text
    assert "FRESH" in text


def test_stale_sources_are_labeled(analysis_report) -> None:
    stale = SourceMetadata(
        provider="alpha_vantage",
        dataset="CASH_FLOW",
        symbol="ACME",
        source_url="fake://cash",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2025, 12, 1, tzinfo=UTC),
        stale=True,
    )
    report = analysis_report.model_copy(update={"sources": (*analysis_report.sources, stale)})
    text = render_text(report)
    assert "STALE" in text
    assert "2026-01-01T00:00:00+00:00" in text


def test_unavailable_model_shows_reason_and_inconclusive(analysis_report) -> None:
    unavailable = ModelResult(
        model=ModelName.STABLE_FCFE, unavailable_reason="missing reported EPS"
    )
    models = (unavailable, *analysis_report.current.models[1:])
    current = analysis_report.current.model_copy(update={"models": models})
    report = analysis_report.model_copy(
        update={"current": current, "screen_state": ScreenState.INCONCLUSIVE}
    )
    text = render_text(report)
    assert "INCONCLUSIVE" in text
    assert "missing reported EPS" in text


def test_explicit_and_terminal_pv_are_reported_separately(analysis_report) -> None:
    text = render_text(analysis_report)
    for result in analysis_report.current.models:
        shares = analysis_report.current.period.shares_outstanding
        assert shares is not None
        explicit_per_share = result.explicit_stage_pv / shares
        assert f"{explicit_per_share:,.2f}" in text
        terminal_per_share = result.terminal_value_pv / shares
        assert f"{terminal_per_share:,.2f}" in text


def test_assumptions_include_resolved_defaults(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "6.00%" in text
    assert "2.50%" in text
    assert "25.00%" in text
    assert "margin of safety" in text.lower()


def test_all_three_sensitivity_grids_are_labeled(analysis_report) -> None:
    text = render_text(analysis_report)
    assert text.count("Sensitivity") >= 3
    for label in MODEL_LABELS:
        grid_header = f"Sensitivity - {label}"
        assert grid_header in text


def test_unavailable_sensitivity_cell_renders_na(analysis_report) -> None:
    hole = SensitivityPoint(
        model=ModelName.STABLE_FCFE,
        discount_rate_delta=0.0,
        terminal_growth_delta=0.0,
        unavailable_reason="cost of equity below growth",
    )
    cells = list(analysis_report.current.sensitivity)
    cells[0] = hole
    current = analysis_report.current.model_copy(update={"sensitivity": tuple(cells)})
    report = analysis_report.model_copy(update={"current": current})
    text = render_text(report)
    assert "N/A" in text


def test_zero_option_value_is_disclosed(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "equity option value" in text.lower()
    assert "0.00" in text


def test_financing_indicator_is_non_causal(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "This shows co-movement, not that debt caused growth." in text


def test_diagnostics_table_covers_history(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "annual" in text
    assert "TTM" in text
    for name in ("ROE", "ROC", "operating margin"):
        assert name in text


def test_reconstruction_caveat_present(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "not a look-ahead-free backtest" in text


def test_json_includes_sensitivity_and_warnings(analysis_report) -> None:
    rendered = render_json(analysis_report)
    assert '"sensitivity"' in rendered
    assert '"warnings"' in rendered
    parsed_date = f'"{analysis_report.as_of.isoformat()}"'
    assert parsed_date in rendered
    assert isinstance(AnalysisReport, type)
    assert date(2026, 2, 1) == analysis_report.as_of
