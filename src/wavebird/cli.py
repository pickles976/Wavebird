import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
from pydantic import ValidationError

from wavebird.alpha_vantage import AlphaDataset, AlphaVantageClient
from wavebird.cache import SQLiteCache
from wavebird.config import Settings, load_assumptions
from wavebird.damodaran import DamodaranClient
from wavebird.errors import WavebirdError
from wavebird.plotting import parse_window, plot_report
from wavebird.reporting import render_json, render_text
from wavebird.service import AnalysisService
from wavebird.shiller import ShillerClient

COMPANY_DATASETS = (
    AlphaDataset.INCOME_STATEMENT,
    AlphaDataset.CASH_FLOW,
    AlphaDataset.BALANCE_SHEET,
    AlphaDataset.EARNINGS,
    AlphaDataset.WEEKLY_ADJUSTED,
)


@dataclass
class Runtime:
    settings: Settings
    cache: SQLiteCache
    http: httpx.Client
    alpha: AlphaVantageClient
    rates: DamodaranClient
    shiller: ShillerClient
    service: AnalysisService

    def close(self) -> None:
        self.http.close()
        self.cache.close()


def build_runtime(settings: Settings) -> Runtime:
    cache = SQLiteCache(settings.cache_path)
    http = httpx.Client()
    api_key = (
        settings.alpha_vantage_api_key.get_secret_value()
        if settings.alpha_vantage_api_key is not None
        else None
    )
    alpha = AlphaVantageClient(
        cache=cache,
        api_key=api_key,
        http=http,
        base_url=str(settings.alpha_base_url),
    )
    rates = DamodaranClient(
        cache=cache,
        http=http,
        annual_url=str(settings.damodaran_annual_url),
        monthly_url=str(settings.damodaran_monthly_url),
    )
    shiller = ShillerClient(cache=cache, http=http)
    service = AnalysisService(alpha=alpha, rates=rates, shiller=shiller)
    return Runtime(
        settings=settings,
        cache=cache,
        http=http,
        alpha=alpha,
        rates=rates,
        shiller=shiller,
        service=service,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wavebird",
        description="Cache-first comparative DCF analysis for mature US-listed companies",
    )
    parser.add_argument(
        "--edit-assumptions",
        dest="edit_assumptions",
        action="store_true",
        help="open the assumptions editor at the start of the interactive session",
    )
    parser.add_argument(
        "--as-of", dest="as_of", default=None, help="valuation date YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--refresh",
        dest="refresh",
        action="store_true",
        help="bypass fresh cache entries in the interactive session",
    )
    commands = parser.add_subparsers(dest="command")
    interactive = commands.add_parser(
        "interactive", help="guided interactive session (default with no arguments)"
    )
    interactive.add_argument(
        "--edit-assumptions",
        dest="edit_assumptions",
        action="store_true",
        help="open the assumptions editor at the start of the session",
    )
    interactive.add_argument(
        "--as-of", dest="as_of", default=None, help="valuation date YYYY-MM-DD (default: today)"
    )
    interactive.add_argument(
        "--refresh",
        dest="refresh",
        action="store_true",
        help="bypass fresh cache entries",
    )

    analyze = commands.add_parser("analyze", help="analyze a symbol against assumptions")
    analyze.add_argument("symbol", help="ticker symbol, e.g. IBM")
    analyze.add_argument(
        "--assumptions", dest="assumptions", required=True, help="TOML assumptions file"
    )
    analyze.add_argument("--as-of", dest="as_of", default=None, help="valuation date YYYY-MM-DD")
    analyze.add_argument("--refresh", action="store_true", help="bypass fresh cache entries")
    analyze.add_argument("--json", action="store_true", help="emit JSON instead of text")
    analyze.add_argument("--plot", dest="plot", default=None, help="write the four-panel plot")
    analyze.add_argument(
        "--window",
        dest="window",
        default=None,
        help="plot time window: 52w, 3y, 5y, 10y, or all (default)",
    )

    cache = commands.add_parser("cache", help="cache management")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_refresh = cache_sub.add_parser("refresh", help="refresh all cached company datasets")
    cache_refresh.add_argument("symbol", help="ticker symbol, e.g. IBM")

    rates = commands.add_parser("rates", help="Damodaran rate data")
    rates_sub = rates.add_subparsers(dest="rates_command", required=True)
    rates_sub.add_parser("refresh", help="refresh annual and monthly rate workbooks")
    return parser


def _run_interactive_command(
    settings: Settings,
    edit_assumptions: bool = False,
    args: argparse.Namespace | None = None,
) -> int:
    from wavebird.interactive import run_from_runtime

    as_of = None
    if args is not None and getattr(args, "as_of", None):
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"invalid --as-of date: {args.as_of}", file=sys.stderr)
            return 2
    refresh = bool(getattr(args, "refresh", False)) if args is not None else False
    runtime = build_runtime(settings)
    try:
        return run_from_runtime(
            runtime.service,
            runtime.cache,
            http=runtime.http,
            edit_assumptions=edit_assumptions,
            as_of=as_of,
            refresh=refresh,
        )
    finally:
        runtime.close()


def _run(args: argparse.Namespace, settings: Settings) -> int:
    if args.command is None or args.command == "interactive":
        return _run_interactive_command(
            settings,
            edit_assumptions=bool(getattr(args, "edit_assumptions", False)),
            args=args,
        )
    if args.command == "analyze":
        return _run_analyze(args, settings)
    if args.command == "cache":
        return _run_cache_refresh(args, settings)
    if args.command == "rates":
        return _run_rates_refresh(settings)
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


def _run_analyze(args: argparse.Namespace, settings: Settings) -> int:
    try:
        assumptions = load_assumptions(Path(args.assumptions))
    except (OSError, ValidationError) as error:
        print(f"invalid assumptions file: {error}", file=sys.stderr)
        return 2
    if args.as_of is not None:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"invalid --as-of date: {args.as_of}", file=sys.stderr)
            return 2
    else:
        as_of = date.today()

    runtime = build_runtime(settings)
    try:
        report = runtime.service.analyze(args.symbol, assumptions, as_of, refresh=args.refresh)
        if args.json:
            print(render_json(report))
        else:
            print(render_text(report))
        if args.plot is not None:
            try:
                window = parse_window(args.window)
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 2
            plot_report(report, Path(args.plot), window=window)
    finally:
        runtime.close()
    return 0


def _run_cache_refresh(args: argparse.Namespace, settings: Settings) -> int:
    symbol = args.symbol.strip().upper()
    runtime = build_runtime(settings)
    try:
        for dataset in COMPANY_DATASETS:
            payload = runtime.alpha.get(dataset, symbol, refresh=True)
            print(f"{dataset.value} {symbol} fetched {payload.source.fetched_at.isoformat()}")
        spy = runtime.alpha.get(AlphaDataset.WEEKLY_ADJUSTED, "SPY", refresh=True)
        print(f"TIME_SERIES_WEEKLY_ADJUSTED SPY fetched {spy.source.fetched_at.isoformat()}")
    finally:
        runtime.close()
    return 0


def _run_rates_refresh(settings: Settings) -> int:
    runtime = build_runtime(settings)
    try:
        point = runtime.rates.get_rates(date.today(), refresh=True)
        shiller_note = ""
        if runtime.shiller is not None:
            fetch = runtime.shiller.get_series(refresh=True)
            latest = fetch.records[-1].date if fetch.records else None
            shiller_note = (
                f", shiller PE through {latest.isoformat()}"
                if latest
                else ", shiller PE unavailable"
            )
        print(
            f"rates refreshed through {point.date.isoformat()} "
            f"(risk-free {point.risk_free_rate:.4f}, ERP {point.equity_risk_premium:.4f})"
            f"{shiller_note}"
        )
    finally:
        runtime.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        code = error.code
        if code is None:
            return 0
        try:
            return int(code)
        except (TypeError, ValueError):
            return 2
    settings = Settings()
    try:
        return _run(args, settings)
    except WavebirdError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code
    except BrokenPipeError:
        return 0


__all__ = ["Runtime", "build_parser", "build_runtime", "main"]
