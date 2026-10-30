from datetime import date
from pathlib import Path
from typing import Any, Protocol

from prompt_toolkit import prompt as tk_prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import PromptSession, radiolist_dialog
from pydantic import ValidationError

from wavebird.alpha_vantage import DAILY_REQUEST_LIMIT, SYMBOL_PATTERN
from wavebird.config import ValuationAssumptions
from wavebird.errors import WavebirdError
from wavebird.models import AnalysisReport
from wavebird.plotting import parse_window, write_report_pdf
from wavebird.reporting import render_json, render_text

DEFAULT_ASSUMPTIONS_PATH = Path("examples/assumptions.toml")

TICKER_SUGGESTIONS = (
    "AAPL",
    "MSFT",
    "GOOG",
    "GOOGL",
    "AMZN",
    "META",
    "BRK.B",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "PG",
    "KO",
    "PEP",
    "DIS",
    "IBM",
    "CVX",
    "WMT",
    "HD",
    "MCD",
)

ASSUMPTION_LABELS = {
    "forecast_years": "Forecast years (explicit stage)",
    "high_growth_rate": "High growth rate (decimal, e.g. 0.06)",
    "stable_growth_rate": "Stable growth rate (decimal)",
    "stable_roe": "Stable ROE (decimal)",
    "stable_roc": "Stable ROC (decimal)",
    "stable_beta": "Stable beta",
    "pretax_cost_of_debt": "Pre-tax cost of debt (decimal)",
    "target_debt_weight": "Target debt weight (0 to 1)",
    "margin_of_safety": "Margin of safety (0 to 1)",
    "capex_growth_rate": "CapEx growth rate (blank = high growth)",
    "depreciation_growth_rate": "Depreciation growth rate (blank = high growth)",
    "revenue_growth_rate": "Revenue growth rate (blank = high growth)",
    "equity_option_value": "Equity option value (currency)",
}

_INT_FIELDS = {"forecast_years"}
_OPTIONAL_FIELDS = {"capex_growth_rate", "depreciation_growth_rate", "revenue_growth_rate"}

_RUN_CHOICE = "__run__"
_RESET_CHOICE = "__reset__"


def quota_banner(cache: Any, now: Any = None) -> str:
    used = cache.requests_today("alpha_vantage", now) if cache is not None else 0
    return (
        f"Alpha Vantage free tier: {DAILY_REQUEST_LIMIT} requests/day (5 requests/minute). "
        f"Wavebird made {used} today; only requests made through Wavebird are counted."
    )


def ticker_suggestions(cache: Any, http: Any, ui: "InteractiveUI") -> list[str]:
    symbols = {*TICKER_SUGGESTIONS, *cache.distinct_symbols("alpha_vantage")}
    if http is None:
        return sorted(symbols)
    try:
        from wavebird.directory import TickerDirectoryClient

        symbols.update(TickerDirectoryClient(cache, http).load())
    except WavebirdError:
        ui.notify("SEC ticker directory unavailable; using cached and built-in suggestions.")
    return sorted(symbols)


class TickerCompleter(Completer):
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = sorted({symbol.strip().upper() for symbol in symbols})

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.strip().upper()
        if not text:
            return
        for symbol in self.symbols:
            if symbol.startswith(text):
                yield Completion(symbol, start_position=-len(document.text_before_cursor))


def assumption_fields() -> list[tuple[str, str, Any]]:
    return [
        (name, ASSUMPTION_LABELS[name], field.annotation)
        for name, field in ValuationAssumptions.model_fields.items()
        if name in ASSUMPTION_LABELS
    ]


def load_initial_values(path: Path = DEFAULT_ASSUMPTIONS_PATH) -> dict[str, Any]:
    try:
        import tomllib

        with path.open("rb") as stream:
            return tomllib.load(stream)
    except FileNotFoundError:
        return {}


def apply_field(
    values: dict[str, Any],
    name: str,
    raw: str,
) -> tuple[dict[str, Any], str | None]:
    candidate = dict(values)
    text = raw.strip()
    if name in _OPTIONAL_FIELDS and not text:
        candidate.pop(name, None)
    else:
        try:
            candidate[name] = int(text) if name in _INT_FIELDS else float(text)
        except ValueError:
            return values, f"{name}: expected a number, got {raw!r}"
    try:
        ValuationAssumptions.model_validate(candidate)
    except ValidationError as error:
        substantive = [e for e in error.errors() if e["type"] != "missing"]
        if substantive:
            first = substantive[0]
            location = ".".join(str(part) for part in first["loc"]) or name
            return values, f"{location}: {first['msg']}"
    return candidate, None


def _current_display(values: dict[str, Any], name: str) -> str:
    if name not in values:
        if name in _OPTIONAL_FIELDS:
            return "default (high growth)"
        return "not set"
    value = values[name]
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _menu_options(values: dict[str, str]) -> list[tuple[str, str]]:
    # A radiolist always has a selection, so the run action leads the list: pressing
    # Enter on a fresh dialog runs the analysis instead of re-opening a field.
    options = [(_RUN_CHOICE, "Review & run analysis")]
    options.extend(
        (name, f"{label}  [now: {_current_display(values, name)}]")
        for name, label, _ in assumption_fields()
    )
    options.append((_RESET_CHOICE, "Reload example assumptions file"))
    return options


def edit_assumptions(ui: "InteractiveUI", values: dict[str, Any]) -> ValuationAssumptions | None:
    while True:
        choice = ui.radiolist("Assumptions - pick a field to edit", _menu_options(values))
        if choice is None:
            return None
        if choice == _RESET_CHOICE:
            values = load_initial_values()
            ui.notify("Reloaded example assumptions.")
            continue
        if choice == _RUN_CHOICE:
            try:
                return ValuationAssumptions.model_validate(values)
            except ValidationError as error:
                first = error.errors()[0]
                location = ".".join(str(part) for part in first["loc"])
                ui.notify(f"Cannot run yet: {location}: {first['msg']}")
                continue
        label = ASSUMPTION_LABELS[choice]
        raw = ui.prompt(
            f"{label} [now: {_current_display(values, choice)}] (blank = keep current)",
            default="",
        )
        if not raw.strip():
            continue
        values, error = apply_field(values, choice, raw)
        if error is not None:
            ui.notify(f"Not applied: {error}")


def _ask_symbol(ui: "InteractiveUI", suggestions: list[str]) -> str:
    completer = TickerCompleter([*TICKER_SUGGESTIONS, *suggestions])
    while True:
        raw = ui.prompt("Ticker", completer=completer)
        symbol = raw.strip().upper()
        if symbol and SYMBOL_PATTERN.fullmatch(symbol):
            return symbol
        ui.notify("Tickers may contain letters, digits, '.', and '-' only.")


def yes_no(ui: "InteractiveUI", question: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = ui.prompt(f"{question} {suffix}").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        ui.notify("Please answer y or n.")


class InteractiveUI(Protocol):
    def prompt(
        self,
        message: str,
        default: str | None = None,
        completer: Completer | None = None,
        validator: Any = None,
    ) -> str: ...

    def radiolist(self, title: str, options: list[tuple[str, str]]) -> str | None: ...

    def notify(self, message: str) -> None: ...


class PromptToolkitUI:
    def __init__(self) -> None:
        self.session = PromptSession()

    def prompt(self, message, default=None, completer=None, validator=None):
        text = f"{message}> "
        if completer is None:
            # Stateless prompt: no session state, so no completion menu can bleed
            # into yes/no and path questions.
            return tk_prompt(text, default=default or "", validator=validator)
        return self.session.prompt(
            text, default=default or "", completer=completer, validator=validator
        )

    def radiolist(self, title, options):
        return radiolist_dialog(
            title=title,
            text="Select with arrow keys; Enter confirms; Esc cancels.",
            values=options,
        ).run()

    def notify(self, message):
        print(message)


def run_interactive(
    ui: InteractiveUI,
    service: Any,
    suggestions: list[str],
    as_of_default: date | None = None,
    assumptions_path: Path = DEFAULT_ASSUMPTIONS_PATH,
    open_editor: bool = False,
    refresh_default: bool = False,
) -> int:
    symbol = _ask_symbol(ui, suggestions)
    values = load_initial_values(assumptions_path)
    if values:
        ui.notify(f"Loaded {len(values)} assumption(s) from {assumptions_path}")
    assumptions: ValuationAssumptions | None = None
    try:
        assumptions = ValuationAssumptions.model_validate(values)
    except ValidationError:
        ui.notify(f"Cannot load assumptions from {assumptions_path}; opening the editor.")
    if open_editor or assumptions is None:
        assumptions = edit_assumptions(ui, values)
        if assumptions is None:
            ui.notify("Cancelled.")
            return 0
    as_of = as_of_default or date.today()
    report: AnalysisReport = service.analyze(symbol, assumptions, as_of, refresh=refresh_default)
    ui.notify(render_text(report))

    if yes_no(ui, "Save PDF report?", default=False):
        window: Any = None
        while True:
            raw = ui.prompt("Plot window 52w/3y/5y/10y (blank = all history)", default="")
            try:
                window = parse_window(raw)
                break
            except ValueError as error:
                ui.notify(str(error))
        path = ui.prompt("Report path", default=f"{symbol}.pdf")
        write_report_pdf(report, Path(path), window=window)
        ui.notify(f"Report written to {path}")
    if yes_no(ui, "Save JSON report?", default=False):
        path = ui.prompt("JSON path", default=f"{symbol}.json")
        Path(path).write_text(render_json(report))
        ui.notify(f"JSON written to {path}")
    return 0


def run_from_runtime(
    service: Any,
    cache: Any,
    ui: InteractiveUI | None = None,
    now: Any = None,
    http: Any = None,
    edit_assumptions: bool = False,
    as_of: Any = None,
    refresh: bool = False,
) -> int:
    active_ui = ui if ui is not None else PromptToolkitUI()
    moment = now() if callable(now) else now
    try:
        active_ui.notify(quota_banner(cache, moment))
        suggestions = ticker_suggestions(cache, http, active_ui)
        return run_interactive(
            active_ui,
            service,
            suggestions=suggestions,
            open_editor=edit_assumptions,
            as_of_default=as_of,
            refresh_default=refresh,
        )
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    except WavebirdError as error:
        print(str(error))
        return error.exit_code
