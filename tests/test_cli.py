import json
from datetime import date
from pathlib import Path

from conftest import FAKE_NOW, FakeAlphaProvider, FakeRatesProvider, build_fake_payloads

from wavebird.alpha_vantage import AlphaDataset
from wavebird.cli import main
from wavebird.config import ValuationAssumptions
from wavebird.errors import InvalidTickerError
from wavebird.models import AnalysisReport
from wavebird.service import AnalysisService

VALID_ASSUMPTIONS = """
forecast_years = 5
high_growth_rate = 0.06
stable_growth_rate = 0.025
stable_roe = 0.12
stable_roc = 0.10
stable_beta = 1.0
pretax_cost_of_debt = 0.05
target_debt_weight = 0.20
margin_of_safety = 0.25
"""


class FailingService:
    def analyze(self, *args, **kwargs):
        raise InvalidTickerError("invalid ticker")


class FailingRuntime:
    service = FailingService()

    def close(self) -> None:
        pass


class RecordingService:
    def __init__(self):
        self.calls = []

    def analyze(self, symbol, assumptions, as_of, refresh=False) -> AnalysisReport:
        self.calls.append((symbol, assumptions, as_of, refresh))
        real = AnalysisService(
            alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
        )
        return real.analyze(symbol, assumptions, as_of, refresh=refresh)


class FakeCache:
    def distinct_symbols(self, provider: str) -> list[str]:
        return ["GOOG"]


class FakeRuntime:
    http = None

    shiller = None

    def __init__(self, service=None) -> None:
        self.cache = FakeCache()
        self.alpha = FakeAlphaProvider(build_fake_payloads())
        self.rates = FakeRatesProvider()
        self.service = service if service is not None else RecordingService()

    def close(self) -> None:
        pass


def write_assumptions(tmp_path: Path) -> Path:
    path = tmp_path / "assumptions.toml"
    path.write_text(VALID_ASSUMPTIONS)
    return path


def test_analyze_requires_assumptions(capsys) -> None:
    code = main(["analyze", "AAPL"])
    assert code == 2
    assert "--assumptions" in capsys.readouterr().err


def test_domain_error_uses_stable_exit_code(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: FailingRuntime())
    path = write_assumptions(tmp_path)
    code = main(["analyze", "BAD", "--assumptions", str(path)])
    assert code == 5
    captured = capsys.readouterr()
    assert "invalid ticker" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_analyze_renders_text_report(monkeypatch, tmp_path: Path, capsys) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    path = write_assumptions(tmp_path)
    code = main(["analyze", "aapl", "--assumptions", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Wavebird analysis for AAPL" in out


def test_analyze_json_output(monkeypatch, tmp_path: Path, capsys) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    path = write_assumptions(tmp_path)
    code = main(["analyze", "AAPL", "--assumptions", str(path), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["symbol"] == "AAPL"
    assert parsed["screen_state"] == "PASSES SCREEN"


def test_analyze_writes_plot(monkeypatch, tmp_path: Path, capsys) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    path = write_assumptions(tmp_path)
    plot = tmp_path / "out" / "aapl.png"
    code = main(["analyze", "AAPL", "--assumptions", str(path), "--plot", str(plot)])
    assert code == 0
    assert plot.stat().st_size > 0


def test_analyze_passes_as_of_and_refresh(monkeypatch, tmp_path: Path) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    path = write_assumptions(tmp_path)
    code = main(
        [
            "analyze",
            "AAPL",
            "--assumptions",
            str(path),
            "--as-of",
            "2026-01-15",
            "--refresh",
        ]
    )
    assert code == 0
    symbol, assumptions, as_of, refresh = runtime.service.calls[0]
    assert symbol == "AAPL"
    assert isinstance(assumptions, ValuationAssumptions)
    assert as_of == date(2026, 1, 15)
    assert refresh is True


def test_cache_refresh_requests_all_datasets(monkeypatch, tmp_path: Path, capsys) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    code = main(["cache", "refresh", "ibm"])
    assert code == 0
    calls = runtime.alpha.calls
    assert {(call[0], call[1]) for call in calls} == {
        (dataset.value, "IBM")
        for dataset in (
            AlphaDataset.INCOME_STATEMENT,
            AlphaDataset.CASH_FLOW,
            AlphaDataset.BALANCE_SHEET,
            AlphaDataset.EARNINGS,
            AlphaDataset.WEEKLY_ADJUSTED,
        )
    } | {(AlphaDataset.WEEKLY_ADJUSTED.value, "SPY")}
    assert all(call[2] is True for call in calls)
    out = capsys.readouterr().out
    assert FAKE_NOW.isoformat() in out


def test_rates_refresh_prints_dates(monkeypatch, capsys) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    code = main(["rates", "refresh"])
    assert code == 0
    assert runtime.rates.calls[0][1] is True
    assert "rates refreshed" in capsys.readouterr().out.lower()


def test_invalid_assumptions_file_exits_cleanly(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: FakeRuntime())
    path = tmp_path / "bad.toml"
    path.write_text("forecast_years = 5\nunknown = 1\n")
    code = main(["analyze", "AAPL", "--assumptions", str(path)])
    assert code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_help_returns_zero(capsys) -> None:
    code = main(["--help"])
    assert code == 0
    out = capsys.readouterr().out
    for command in ("analyze", "cache", "rates"):
        assert command in out


def test_bare_invocation_launches_interactive(monkeypatch) -> None:
    import wavebird.interactive as interactive_module

    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    seen = {}

    def fake_run_from_runtime(
        service, cache, http=None, edit_assumptions=False, as_of=None, refresh=False
    ):
        seen["service"] = service
        seen["symbols"] = cache.distinct_symbols("alpha_vantage")
        seen["edit_assumptions"] = edit_assumptions
        return 0

    monkeypatch.setattr(interactive_module, "run_from_runtime", fake_run_from_runtime)
    code = main([])
    assert code == 0
    assert seen["symbols"] == ["GOOG"]
    assert seen["service"] is runtime.service
    assert seen["edit_assumptions"] is False


def test_edit_assumptions_flag_routes(monkeypatch) -> None:
    import wavebird.interactive as interactive_module

    seen = {}

    def fake_run_from_runtime(
        service, cache, http=None, edit_assumptions=False, as_of=None, refresh=False
    ):
        seen["edit_assumptions"] = edit_assumptions
        return 0

    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: FakeRuntime())
    monkeypatch.setattr(interactive_module, "run_from_runtime", fake_run_from_runtime)
    assert main(["--edit-assumptions"]) == 0
    assert seen["edit_assumptions"] is True
    assert main(["interactive", "--edit-assumptions"]) == 0
    assert seen["edit_assumptions"] is True


def test_interactive_subcommand_routes(monkeypatch) -> None:
    import wavebird.interactive as interactive_module

    runtime = FakeRuntime()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    monkeypatch.setattr(
        interactive_module,
        "run_from_runtime",
        lambda service, cache, http=None, edit_assumptions=False, as_of=None, refresh=False: 0,
    )
    assert main(["interactive"]) == 0


def test_analyze_window_flag_reaches_plot(monkeypatch, tmp_path) -> None:
    from datetime import timedelta

    seen = {}

    from conftest import FakeAlphaProvider, FakeRatesProvider

    import wavebird.cli as cli_module
    from wavebird.service import AnalysisService

    def fake_plot_report(report, destination, window=None):
        seen["window"] = window
        destination.write_bytes(b"png")
        return destination

    monkeypatch.setattr(cli_module, "plot_report", fake_plot_report)
    monkeypatch.setattr(
        "wavebird.cli.build_runtime",
        lambda settings: FakeRuntime(
            AnalysisService(
                alpha=FakeAlphaProvider(build_fake_payloads()),
                rates=FakeRatesProvider(),
            )
        ),
    )
    assumptions = tmp_path / "assumptions.toml"
    assumptions.write_text(
        "forecast_years = 5\n"
        "high_growth_rate = 0.06\n"
        "stable_growth_rate = 0.025\n"
        "stable_roe = 0.12\n"
        "stable_roc = 0.10\n"
        "stable_beta = 1.0\n"
        "pretax_cost_of_debt = 0.05\n"
        "target_debt_weight = 0.20\n"
        "margin_of_safety = 0.25\n"
    )
    code = main(
        [
            "analyze",
            "acme",
            "--assumptions",
            str(assumptions),
            "--plot",
            str(tmp_path / "out.png"),
            "--window",
            "3y",
        ]
    )
    assert code == 0
    assert seen["window"] == timedelta(days=3 * 365)


def test_interactive_as_of_and_refresh_flags(monkeypatch) -> None:
    from datetime import date

    import wavebird.interactive as interactive_module

    seen = {}

    def fake_run_from_runtime(
        service, cache, http=None, edit_assumptions=False, as_of=None, refresh=False
    ):
        seen["as_of"] = as_of
        seen["refresh"] = refresh
        return 0

    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: FakeRuntime())
    monkeypatch.setattr(interactive_module, "run_from_runtime", fake_run_from_runtime)
    assert main(["--as-of", "2024-06-30", "--refresh"]) == 0
    assert seen["as_of"] == date(2024, 6, 30)
    assert seen["refresh"] is True
    assert main(["interactive", "--as-of", "2024-06-30"]) == 0
    assert seen["refresh"] is False


def test_rates_refresh_refreshes_shiller(monkeypatch, tmp_path) -> None:
    seen = {"shiller": 0}

    from wavebird.shiller import ShillerFetch

    class FakeShiller:
        def get_series(self, refresh: bool = False):
            seen["shiller"] += 1
            assert refresh is True
            return ShillerFetch(records=(), source=None)

    runtime = FakeRuntime()
    runtime.shiller = FakeShiller()
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: runtime)
    assert main(["rates", "refresh"]) == 0
    assert seen["shiller"] == 1
