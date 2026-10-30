from datetime import date

from conftest import FakeAlphaProvider, FakeRatesProvider
from prompt_toolkit.document import Document

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.interactive import (
    TickerCompleter,
    _menu_options,
    apply_field,
    assumption_fields,
    edit_assumptions,
    load_initial_values,
    quota_banner,
    run_interactive,
    yes_no,
)
from wavebird.service import AnalysisService


class FakeUI:
    def __init__(self, prompts=None, radiolist_results=None):
        self.prompts = list(prompts or [])
        self.radiolist_results = list(radiolist_results or [])
        self.prompt_calls = []
        self.radiolist_calls = []
        self.notifications = []

    def prompt(self, message, default=None, completer=None, validator=None):
        self.prompt_calls.append(message)
        return self.prompts.pop(0) or (default or "")

    def radiolist(self, title, options):
        self.radiolist_calls.append((title, options))
        return self.radiolist_results.pop(0)

    def notify(self, message):
        self.notifications.append(message)


def test_assumption_fields_enumerate_every_field() -> None:
    names = [name for name, _, _ in assumption_fields()]
    assert names == [
        "forecast_years",
        "high_growth_rate",
        "stable_growth_rate",
        "stable_roe",
        "stable_roc",
        "stable_beta",
        "pretax_cost_of_debt",
        "target_debt_weight",
        "margin_of_safety",
        "capex_growth_rate",
        "depreciation_growth_rate",
        "revenue_growth_rate",
        "equity_option_value",
    ]


def test_apply_field_updates_value_and_validates() -> None:
    values = load_initial_values()
    updated, error = apply_field(values, "forecast_years", "3")
    assert error is None
    assert updated["forecast_years"] == 3


def test_apply_field_rejects_invalid_input_without_applying() -> None:
    values = load_initial_values()
    updated, error = apply_field(values, "margin_of_safety", "1.5")
    assert error is not None
    assert updated == values
    updated, error = apply_field(values, "stable_growth_rate", "abc")
    assert error is not None
    assert updated == values


def test_apply_field_blank_clears_optional_rate() -> None:
    values = {"capex_growth_rate": 0.1}
    updated, error = apply_field(values, "capex_growth_rate", "")
    assert error is None
    assert "capex_growth_rate" not in updated


def test_ticker_completer_suggests_cached_symbols() -> None:
    completer = TickerCompleter(["goog", "ACME", "AAPL"])
    completions = list(completer.get_completions(Document("go"), complete_event=None))
    assert [c.text for c in completions] == ["GOOG"]


def test_distinct_symbols_from_cache(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for symbol in ("ZM", "GOOG", "GOOG", "AAPL"):
        cache.put(
            CacheKey("alpha_vantage", "EARNINGS", symbol, f"EARNINGS:{symbol}"),
            {},
            now,
            now + timedelta(days=1),
            "fake://",
        )
    assert cache.distinct_symbols("alpha_vantage") == ["AAPL", "GOOG", "ZM"]
    cache.close()


def test_yes_no_parses_and_reprompts() -> None:
    ui = FakeUI(prompts=["banana", "n"])
    assert yes_no(ui, "Save plot?", default=True) is False
    ui = FakeUI(prompts=["y"])
    assert yes_no(ui, "Save plot?", default=False) is True
    ui = FakeUI(prompts=[""])
    assert yes_no(ui, "Save plot?", default=True) is True


def test_menu_puts_run_first() -> None:
    options = _menu_options(load_initial_values())
    assert options[0][0] == "__run__"


def test_blank_value_prompt_keeps_value() -> None:
    values = load_initial_values()
    ui = FakeUI(prompts=[""], radiolist_results=["forecast_years", "__run__"])
    result = edit_assumptions(ui, values)
    assert result is not None
    assert result.forecast_years == 5
    assert "blank" in ui.prompt_calls[0]


def test_run_interactive_default_flow_skips_editor(tmp_path, monkeypatch) -> None:
    from conftest import build_fake_payloads

    monkeypatch.chdir(tmp_path)
    assumptions_path = tmp_path / "assumptions.toml"
    assumptions_path.write_text(
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
    payloads = build_fake_payloads()
    service = AnalysisService(alpha=FakeAlphaProvider(payloads), rates=FakeRatesProvider())
    ui = FakeUI(
        prompts=[
            "acme",  # ticker
            "n",  # save plot?
            "n",  # save json?
        ],
    )
    code = run_interactive(
        ui,
        service,
        suggestions=["ACME", "GOOG"],
        as_of_default=date(2026, 2, 1),
        assumptions_path=assumptions_path,
    )
    assert code == 0
    assert ui.radiolist_calls == []
    joined = "\n".join(ui.notifications)
    assert "Wavebird analysis for ACME" in joined
    assert "as of 2026-02-01" in joined
    assert not any("As-of" in call for call in ui.prompt_calls)
    assert not any("cache" in call.lower() for call in ui.prompt_calls)


def test_invalid_assumptions_file_opens_editor(tmp_path) -> None:
    from conftest import build_fake_payloads

    service = AnalysisService(
        alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
    )
    ui = FakeUI(prompts=["acme"], radiolist_results=[None])
    code = run_interactive(
        ui,
        service,
        suggestions=["ACME"],
        as_of_default=date(2026, 2, 1),
        assumptions_path=tmp_path / "missing.toml",
    )
    assert code == 0
    assert len(ui.radiolist_calls) == 1
    assert "Cancelled" in "\n".join(ui.notifications)


def test_run_interactive_full_flow(tmp_path, monkeypatch) -> None:
    from conftest import build_fake_payloads

    monkeypatch.chdir(tmp_path)
    assumptions_path = tmp_path / "assumptions.toml"
    assumptions_path.write_text(
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
    payloads = build_fake_payloads()
    service = AnalysisService(alpha=FakeAlphaProvider(payloads), rates=FakeRatesProvider())
    ui = FakeUI(
        prompts=[
            "acme",  # ticker
            "y",  # save plot?
            "",  # plot window (blank = all)
            "",  # plot path (default acme.png)
            "y",  # save json?
            "",  # json path (default acme.json)
        ],
        radiolist_results=["__run__"],
    )
    code = run_interactive(
        ui,
        service,
        suggestions=["ACME", "GOOG"],
        as_of_default=date(2026, 2, 1),
        assumptions_path=assumptions_path,
        open_editor=True,
    )
    assert code == 0
    assert (tmp_path / "ACME.png").stat().st_size > 0
    assert (tmp_path / "ACME.json").exists()
    joined = "\n".join(ui.notifications)
    assert "Wavebird analysis for ACME" in joined
    assert "PASSES SCREEN" in joined


def test_run_interactive_cancel_assumptions_returns_zero() -> None:
    from conftest import build_fake_payloads

    service = AnalysisService(
        alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
    )
    ui = FakeUI(prompts=["acme"], radiolist_results=[None])
    code = run_interactive(
        ui,
        service,
        suggestions=["ACME"],
        as_of_default=date(2026, 2, 1),
        open_editor=True,
    )
    assert code == 0
    assert "Cancelled" in "\n".join(ui.notifications)


def test_quota_banner_mentions_limit_and_local_usage(tmp_path) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    for _ in range(6):
        cache.record_request("alpha_vantage", now)
    banner = quota_banner(cache, now)
    assert "25 requests/day" in banner
    assert "6" in banner
    assert "only" in banner
    cache.close()


def test_launch_shows_quota_banner(tmp_path) -> None:
    from datetime import UTC, datetime

    from conftest import build_fake_payloads

    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.record_request("alpha_vantage", now)
    service = AnalysisService(
        alpha=FakeAlphaProvider(build_fake_payloads()), rates=FakeRatesProvider()
    )
    ui = FakeUI(prompts=["acme", "n", "n"])
    from wavebird.interactive import run_from_runtime

    code = run_from_runtime(service, cache, ui=ui, now=lambda: now)
    assert code == 0
    assert "25 requests/day" in ui.notifications[0]
    assert "made 1" in ui.notifications[0]
    assert ui.radiolist_calls == []
    cache.close()


def test_prompt_without_completer_is_stateless(monkeypatch) -> None:
    from wavebird import interactive

    seen = {}
    monkeypatch.setattr(
        interactive, "tk_prompt", lambda text, default=None, **kwargs: seen.update(text=text)
    )
    ui = interactive.PromptToolkitUI()
    ui.prompt("Save plot?", default="x")
    assert "Save plot?" in seen["text"]
