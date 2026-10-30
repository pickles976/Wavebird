# Wavebird Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy scripts with a reproducible Python 3.12-3.14 CLI that caches provider data, evaluates three Damodaran-style DCF models, and compares annual/quarterly intrinsic values, market capitalization, debt, and operating growth.

**Architecture:** A cache-first service coordinates direct Alpha Vantage HTTP calls and validated Damodaran workbook imports. Pydantic models form the boundary between providers, normalized financial periods, pure valuation functions, reporting, and plotting. Every financial formula is a pure function with Damodaran workbook regression tests; network, SQLite, CLI, and plotting are tested separately.

**Tech Stack:** CPython 3.12-3.14, uv, Pydantic v2, pydantic-settings, httpx, stdlib sqlite3/tomllib, pandas with python-calamine, matplotlib, pytest, Ruff, ty.

**Spec:** `docs/superpowers/specs/2026-10-30-wavebird-modernization-design.md`

## Global Constraints

- Read the approved spec before editing; formulas, screen states, stale-data policy, and plot caveats are contractual.
- Declare Python `>=3.12,<3.15`; run the final suite on Python 3.12, 3.13, and 3.14.
- Keep runtime dependencies to `httpx`, `pydantic`, `pydantic-settings`, `pandas`, `python-calamine`, and `matplotlib`.
- Use direct Alpha Vantage HTTP. Do not add the `alpha-vantage` SDK, an ORM, an async stack, or a CLI framework.
- Accept only USD financial records. Do not coerce missing provider values to zero.
- Use rates as decimal fractions and retain full precision until presentation.
- Never use market price to infer growth, target debt weight, cost of debt, or terminal assumptions.
- Never average the three model values.
- Use cache data before HTTP. Stale fallback is allowed only for transport/quota failures and must be labeled.
- Keep provider parsing, SQLite, valuation math, orchestration, reporting, and plotting independently testable.
- Implement each task test-first and commit only the files named in that task.

## File Responsibility Map

- `pyproject.toml`: packaging, console script, dependencies, Ruff and pytest configuration.
- `uv.lock`: exact cross-version dependency resolution.
- `src/wavebird/__init__.py`: package version only.
- `src/wavebird/errors.py`: typed domain errors and stable CLI exit codes.
- `src/wavebird/config.py`: environment settings, TOML assumptions loading, assumption validation.
- `src/wavebird/models.py`: provider-independent Pydantic domain and result models.
- `src/wavebird/cache.py`: SQLite schema, freshness lookup, atomic JSON upserts.
- `src/wavebird/alpha_vantage.py`: Alpha transport, error-envelope detection, cache policy, payload parsing.
- `src/wavebird/damodaran.py`: workbook download/parsing, cache policy, dated ERP/risk-free selection.
- `src/wavebird/valuation.py`: financial-period construction, beta/tax derivation, and all pure DCF calculations.
- `src/wavebird/service.py`: end-to-end symbol analysis and historical/current result assembly.
- `src/wavebird/reporting.py`: deterministic text/JSON rendering and screen explanations.
- `src/wavebird/plotting.py`: the four-panel analytical reconstruction.
- `src/wavebird/cli.py`: argparse commands, dependency construction, error-to-exit-code mapping.
- `tests/conftest.py`: deterministic payload/period/assumption builders.
- `tests/test_config.py`: settings and TOML contract.
- `tests/test_models.py`: domain invariants.
- `tests/test_cache.py`: SQLite behavior.
- `tests/test_alpha_vantage.py`: Alpha envelopes, cache integration, normalization.
- `tests/test_damodaran.py`: workbook schema/date parsing and selection.
- `tests/test_valuation.py`: beta, TTM, tax, and three model formulas.
- `tests/test_service.py`: current/history orchestration, stale metadata, screen inputs.
- `tests/test_reporting.py`: text/JSON output and screen/debt interpretations.
- `tests/test_plotting.py`: four axes, labels, gaps, and output file.
- `tests/test_cli.py`: commands and exit codes.
- `examples/assumptions.toml`: explicit sample inputs used by documentation and smoke tests.
- `.github/workflows/ci.yml`: Python 3.12/3.13/3.14 verification matrix.
- `readme.md`: installation, model tradeoffs, commands, provenance, and limitations.

---

### Task 1: Package Foundation, Settings, and Assumptions

**Files:**
- Create: `pyproject.toml`
- Create: `src/wavebird/__init__.py`
- Create: `src/wavebird/errors.py`
- Create: `src/wavebird/config.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`
- Create: `uv.lock`

**Interfaces:**
- Produces: `Settings`, `ValuationAssumptions`, `load_assumptions(path: Path) -> ValuationAssumptions`.
- Produces: `WavebirdError` subclasses carrying `exit_code`.
- Consumes: no application code.

- [ ] **Step 1: Write failing assumptions and settings tests**

```python
# tests/test_config.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from wavebird.config import Settings, ValuationAssumptions, load_assumptions


def valid_values() -> dict[str, float | int]:
    return {
        "forecast_years": 5,
        "high_growth_rate": 0.06,
        "stable_growth_rate": 0.025,
        "stable_roe": 0.12,
        "stable_roc": 0.10,
        "stable_beta": 1.0,
        "pretax_cost_of_debt": 0.05,
        "target_debt_weight": 0.20,
        "margin_of_safety": 0.25,
    }


def test_optional_projection_rates_default_to_high_growth() -> None:
    assumptions = ValuationAssumptions.model_validate(valid_values())
    assert assumptions.capex_growth_rate == 0.06
    assert assumptions.depreciation_growth_rate == 0.06
    assert assumptions.revenue_growth_rate == 0.06
    assert assumptions.equity_option_value == 0.0


def test_terminal_return_must_exceed_stable_growth() -> None:
    values = valid_values() | {"stable_roe": 0.02}
    with pytest.raises(ValidationError, match="stable_roe"):
        ValuationAssumptions.model_validate(values)


def test_load_assumptions_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "assumptions.toml"
    path.write_text("forecast_years = 5\nunknown = 1\n")
    with pytest.raises(ValidationError, match="unknown"):
        load_assumptions(path)


def test_settings_default_cache_uses_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
    settings = Settings(alpha_vantage_api_key=None)
    assert settings.cache_path == Path("/tmp/xdg/wavebird/cache.sqlite3")
```

- [ ] **Step 2: Run the focused test and observe import failure**

Run: `uv run pytest tests/test_config.py -q`

Expected: FAIL because `wavebird.config` does not exist.

- [ ] **Step 3: Create package metadata and tool configuration**

```toml
# pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "wavebird"
version = "0.1.0"
description = "Cache-first comparative DCF analysis for mature US-listed companies"
readme = "readme.md"
requires-python = ">=3.12,<3.15"
dependencies = [
  "httpx>=0.28",
  "matplotlib>=3.10",
  "pandas>=2.2",
  "pydantic>=2.10",
  "pydantic-settings>=2.7",
  "python-calamine>=0.4",
]

[project.scripts]
wavebird = "wavebird.cli:main"

[dependency-groups]
dev = ["pytest>=8.3", "ruff>=0.9", "ty>=0.0.1a6"]

[tool.hatch.build.targets.wheel]
packages = ["src/wavebird"]

[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
```

`src/wavebird/__init__.py` contains only `__version__ = "0.1.0"`.

- [ ] **Step 4: Implement typed errors**

```python
# src/wavebird/errors.py
class WavebirdError(Exception):
    exit_code = 1


class ConfigurationError(WavebirdError):
    exit_code = 2


class AuthenticationError(WavebirdError):
    exit_code = 3


class QuotaError(WavebirdError):
    exit_code = 4


class InvalidTickerError(WavebirdError):
    exit_code = 5


class TransportError(WavebirdError):
    exit_code = 6


class ProviderResponseError(WavebirdError):
    exit_code = 7


class ProviderSchemaError(ProviderResponseError):
    pass


class DataUnavailableError(WavebirdError):
    exit_code = 8


class ValuationMathError(WavebirdError):
    exit_code = 9
```

- [ ] **Step 5: Implement settings and strict TOML validation**

```python
# src/wavebird/config.py
import os
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WAVEBIRD_", extra="forbid", populate_by_name=True
    )

    alpha_vantage_api_key: SecretStr | None = Field(
        default=None, validation_alias="ALPHAVANTAGE_API_KEY"
    )
    alpha_base_url: HttpUrl = HttpUrl("https://www.alphavantage.co/query")
    damodaran_annual_url: HttpUrl = HttpUrl(
        "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"
    )
    damodaran_monthly_url: HttpUrl = HttpUrl(
        "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
    )
    cache_path: Path = Field(default_factory=lambda: Path(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    ) / "wavebird" / "cache.sqlite3")


class ValuationAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    forecast_years: int = Field(ge=1)
    high_growth_rate: float = Field(gt=-1)
    stable_growth_rate: float = Field(gt=-1)
    stable_roe: float = Field(gt=0)
    stable_roc: float = Field(gt=0)
    stable_beta: float
    pretax_cost_of_debt: float = Field(ge=0)
    target_debt_weight: float = Field(ge=0, lt=1)
    margin_of_safety: float = Field(ge=0, lt=1)
    capex_growth_rate: float | None = Field(default=None, gt=-1)
    depreciation_growth_rate: float | None = Field(default=None, gt=-1)
    revenue_growth_rate: float | None = Field(default=None, gt=-1)
    equity_option_value: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_terminal_returns(self) -> Self:
        if self.stable_roe <= self.stable_growth_rate:
            raise ValueError("stable_roe must exceed stable_growth_rate")
        if self.stable_roc <= self.stable_growth_rate:
            raise ValueError("stable_roc must exceed stable_growth_rate")
        object.__setattr__(
            self,
            "capex_growth_rate",
            self.high_growth_rate if self.capex_growth_rate is None else self.capex_growth_rate,
        )
        object.__setattr__(
            self,
            "depreciation_growth_rate",
            self.high_growth_rate
            if self.depreciation_growth_rate is None
            else self.depreciation_growth_rate,
        )
        object.__setattr__(
            self,
            "revenue_growth_rate",
            self.high_growth_rate if self.revenue_growth_rate is None else self.revenue_growth_rate,
        )
        return self


def load_assumptions(path: Path) -> ValuationAssumptions:
    with path.open("rb") as stream:
        return ValuationAssumptions.model_validate(tomllib.load(stream))
```


- [ ] **Step 6: Update ignored generated files and lock dependencies**

Append these exact entries to `.gitignore` while retaining `alphakey.txt`:

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.ty/
*.sqlite3
```

Run: `uv lock && uv sync --group dev`

Expected: `uv.lock` is created and the editable package installs.

- [ ] **Step 7: Run foundation checks**

Run: `uv run pytest tests/test_config.py -q && uv run ruff check src/wavebird/config.py tests/test_config.py && uv run ty check`

Expected: all commands pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/wavebird tests/test_config.py
git commit -m "build: establish typed Python package"
```

---

### Task 2: Provider-independent Domain Models

**Files:**
- Create: `src/wavebird/models.py`
- Create: `tests/test_models.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `PeriodKind`, `ModelName`, `ScreenState`, `SourceMetadata`, `FinancialPeriod`, `PricePoint`, `RatePoint`, `MarketInputs`, `ProjectionPoint`, `SensitivityPoint`, `ModelResult`, `MarketCapPoint`, `AnalysisPoint`, `AnalysisReport`.
- Consumes: `ValuationAssumptions` only through `AnalysisReport`; provider-specific keys remain outside this file.

- [ ] **Step 1: Write model invariant tests**

```python
# tests/test_models.py
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from wavebird.models import ModelName, ModelResult, PeriodKind, SourceMetadata


def test_model_result_requires_value_or_unavailable_reason() -> None:
    with pytest.raises(ValidationError):
        ModelResult(model=ModelName.STABLE_FCFE)


def test_model_result_rejects_value_and_error_together() -> None:
    with pytest.raises(ValidationError):
        ModelResult(
            model=ModelName.STABLE_FCFE,
            value_per_share=10.0,
            intrinsic_equity_value=100.0,
            unavailable_reason="missing EPS",
        )


def test_source_metadata_preserves_stale_state() -> None:
    fetched = datetime(2026, 1, 1, tzinfo=UTC)
    source = SourceMetadata(
        provider="alpha_vantage",
        dataset="BALANCE_SHEET",
        symbol="IBM",
        source_url="https://www.alphavantage.co/query?function=BALANCE_SHEET&symbol=IBM",
        fetched_at=fetched,
        expires_at=fetched,
        stale=True,
    )
    assert source.stale is True
```

- [ ] **Step 2: Run the focused test and observe import failure**

Run: `uv run pytest tests/test_models.py -q`

Expected: FAIL because `wavebird.models` does not exist.

- [ ] **Step 3: Implement immutable domain models**

Use `ConfigDict(frozen=True)` on every model. The essential declarations are:

```python
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wavebird.config import ValuationAssumptions


class PeriodKind(StrEnum):
    ANNUAL = "annual"
    QUARTER = "quarter"
    TTM = "ttm"


class ModelName(StrEnum):
    STABLE_FCFE = "stable_fcfe"
    TWO_STAGE_FCFE = "two_stage_fcfe"
    TWO_STAGE_FCFF = "two_stage_fcff"


class ScreenState(StrEnum):
    PASSES = "PASSES SCREEN"
    DOES_NOT_PASS = "DOES NOT PASS"
    INCONCLUSIVE = "INCONCLUSIVE"


class FinancialPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    fiscal_date: date
    availability_date: date
    period_kind: PeriodKind
    reported_currency: str
    reported_eps: float | None = None
    revenue: float | None = None
    ebit: float | None = None
    pretax_income: float | None = None
    tax_expense: float | None = None
    interest_expense: float | None = None
    net_income: float | None = None
    capex: float | None = None
    depreciation: float | None = None
    current_assets: float | None = None
    cash: float | None = None
    current_liabilities: float | None = None
    current_debt: float | None = None
    total_debt: float | None = None
    book_equity: float | None = None
    dividends: float | None = None
    shares_outstanding: float | None = None
    nwc: float | None = None
    delta_nwc: float | None = None
```

The remaining models use these exact fields:

```python
class SourceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    dataset: str
    symbol: str
    source_url: str
    fetched_at: datetime
    expires_at: datetime
    stale: bool = False


class PricePoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    date: date
    close: float = Field(gt=0)
    adjusted_close: float = Field(gt=0)


class RatePoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    date: date
    risk_free_rate: float
    equity_risk_premium: float
    source: SourceMetadata


class MarketInputs(BaseModel):
    model_config = ConfigDict(frozen=True)
    as_of: date
    beta: float
    risk_free_rate: float
    equity_risk_premium: float


class ProjectionPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    year: int = Field(ge=1)
    cash_flow: float
    present_value: float


class SensitivityPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: ModelName
    discount_rate_delta: float
    terminal_growth_delta: float
    value_per_share: float | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def require_value_xor_reason(self) -> Self:
        if (self.value_per_share is not None) == (self.unavailable_reason is not None):
            raise ValueError("sensitivity point requires a value or reason, exclusively")
        return self


class ModelResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: ModelName
    value_per_share: float | None = None
    intrinsic_equity_value: float | None = None
    explicit_stage_pv: float | None = None
    terminal_value_pv: float | None = None
    terminal_value_share: float | None = None
    discount_to_value: float | None = None
    projections: tuple[ProjectionPoint, ...] = ()
    assumptions: dict[str, float | int | str] = Field(default_factory=dict)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def require_value_xor_reason(self) -> Self:
        values = (
            self.value_per_share,
            self.intrinsic_equity_value,
            self.explicit_stage_pv,
            self.terminal_value_pv,
            self.terminal_value_share,
        )
        has_any_value = any(value is not None for value in values)
        has_all_values = all(value is not None for value in values)
        if self.unavailable_reason is None and not has_all_values:
            raise ValueError("valid model result requires every valuation output")
        if self.unavailable_reason is not None and has_any_value:
            raise ValueError("unavailable model result cannot contain valuation outputs")
        return self


class MarketCapPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    date: date
    market_capitalization: float = Field(gt=0)


class AnalysisPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    period: FinancialPeriod
    market: MarketInputs
    market_price: float = Field(gt=0)
    market_capitalization: float = Field(gt=0)
    models: tuple[ModelResult, ...] = Field(min_length=3, max_length=3)
    diagnostics: dict[str, float | str | None]
    sensitivity: tuple[SensitivityPoint, ...] = ()

    @model_validator(mode="after")
    def require_each_model_once(self) -> Self:
        if {result.model for result in self.models} != set(ModelName):
            raise ValueError("analysis point requires each model exactly once")
        return self


class AnalysisReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    as_of: date
    assumptions: ValuationAssumptions
    current: AnalysisPoint
    annual_history: tuple[AnalysisPoint, ...]
    ttm_history: tuple[AnalysisPoint, ...]
    weekly_market_cap: tuple[MarketCapPoint, ...]
    sources: tuple[SourceMetadata, ...]
    warnings: tuple[str, ...]
    screen_state: ScreenState
```

- [ ] **Step 4: Add deterministic test builders**

```python
# tests/conftest.py
from collections.abc import Callable
from datetime import date

import matplotlib
import pytest

from wavebird.config import ValuationAssumptions
from wavebird.models import FinancialPeriod, PeriodKind

matplotlib.use("Agg")


@pytest.fixture
def assumptions() -> ValuationAssumptions:
    return ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.06,
        stable_growth_rate=0.025,
        stable_roe=0.12,
        stable_roc=0.10,
        stable_beta=1.0,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.20,
        margin_of_safety=0.25,
    )


def _make_period(**overrides: object) -> FinancialPeriod:
    values: dict[str, object] = {
        "symbol": "ACME",
        "fiscal_date": date(2025, 12, 31),
        "availability_date": date(2026, 1, 28),
        "period_kind": PeriodKind.TTM,
        "reported_currency": "USD",
        "reported_eps": 5.0,
        "revenue": 10_000.0,
        "ebit": 1_500.0,
        "pretax_income": 1_300.0,
        "tax_expense": 273.0,
        "interest_expense": 100.0,
        "net_income": 1_027.0,
        "capex": 700.0,
        "depreciation": 400.0,
        "cash": 1_000.0,
        "total_debt": 2_000.0,
        "book_equity": 5_000.0,
        "shares_outstanding": 200.0,
        "nwc": 1_000.0,
        "delta_nwc": 100.0,
    }
    values.update(overrides)
    return FinancialPeriod.model_validate(values)


@pytest.fixture
def make_period() -> Callable[..., FinancialPeriod]:
    return _make_period
```

- [ ] **Step 5: Run model tests and static analysis**

Run: `uv run pytest tests/test_models.py -q && uv run ty check`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/models.py tests/test_models.py tests/conftest.py
git commit -m "feat: define valuation domain models"
```

---

### Task 3: Versioned SQLite JSON Cache

**Files:**
- Create: `src/wavebird/cache.py`
- Create: `tests/test_cache.py`

**Interfaces:**
- Produces: `CacheKey`, `CacheRecord`, `SQLiteCache.get(key)`, `SQLiteCache.put(...)`, and `SQLiteCache.close()`.
- Consumes: JSON-compatible provider payloads; the cache never interprets provider schemas.

- [ ] **Step 1: Write miss, hit, expiry, isolation, and replacement tests**

```python
# tests/test_cache.py
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wavebird.cache import CacheKey, SQLiteCache


def test_cache_miss_hit_expiry_and_atomic_replace(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    key = CacheKey("alpha_vantage", "BALANCE_SHEET", "IBM", "BALANCE_SHEET:IBM")

    assert cache.get(key) is None
    cache.put(key, {"version": 1}, now, now + timedelta(days=7), "https://example.test")
    first = cache.get(key)
    assert first is not None and first.payload == {"version": 1}
    assert first.is_fresh(now + timedelta(days=6))
    assert not first.is_fresh(now + timedelta(days=8))

    cache.put(key, {"version": 2}, now, now + timedelta(days=7), "https://example.test")
    replaced = cache.get(key)
    assert replaced is not None and replaced.payload == {"version": 2}


def test_symbols_are_distinct(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    ibm = CacheKey("alpha_vantage", "CASH_FLOW", "IBM", "CASH_FLOW:IBM")
    aapl = CacheKey("alpha_vantage", "CASH_FLOW", "AAPL", "CASH_FLOW:AAPL")
    cache.put(ibm, {"symbol": "IBM"}, now, now + timedelta(days=7), "https://example.test")
    cache.put(aapl, {"symbol": "AAPL"}, now, now + timedelta(days=7), "https://example.test")
    ibm_record = cache.get(ibm)
    aapl_record = cache.get(aapl)
    assert ibm_record is not None and ibm_record.payload == {"symbol": "IBM"}
    assert aapl_record is not None and aapl_record.payload == {"symbol": "AAPL"}
```

Add a transaction test that opens a second `sqlite3.Connection` after `put` and observes the complete row, never a partially updated row.

- [ ] **Step 2: Run the focused test and observe import failure**

Run: `uv run pytest tests/test_cache.py -q`

Expected: FAIL because `wavebird.cache` does not exist.

- [ ] **Step 3: Implement the schema and typed records**

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    provider TEXT NOT NULL,
    dataset TEXT NOT NULL,
    symbol TEXT NOT NULL,
    request_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY (provider, dataset, symbol, request_key)
) WITHOUT ROWID;
"""

class CacheKey(NamedTuple):
    provider: str
    dataset: str
    symbol: str
    request_key: str


@dataclass(frozen=True)
class CacheRecord:
    key: CacheKey
    payload: dict[str, Any] | list[Any]
    fetched_at: datetime
    expires_at: datetime
    source_url: str

    def is_fresh(self, now: datetime) -> bool:
        return now < self.expires_at
```

`SQLiteCache.__init__` creates parent directories, opens the connection, executes `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL`, then reads `PRAGMA user_version`. Version `0` initializes `SCHEMA` and sets version `1` in one transaction; version `1` opens normally; any other version raises `ConfigurationError` rather than guessing a migration. `put` JSON-serializes with compact separators and sorted keys, then uses one `INSERT ... ON CONFLICT ... DO UPDATE` inside `with self._connection:`. `get` parses ISO UTC timestamps and JSON. Do not expose SQL outside this class.

- [ ] **Step 4: Run the cache tests**

Run: `uv run pytest tests/test_cache.py -q`

Expected: PASS.

- [ ] **Step 5: Run lint and type checks**

Run: `uv run ruff check src/wavebird/cache.py tests/test_cache.py && uv run ty check`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/cache.py tests/test_cache.py
git commit -m "feat: add atomic SQLite response cache"
```

---

### Task 4: Cache-first Alpha Vantage Client

**Files:**
- Create: `src/wavebird/alpha_vantage.py`
- Create: `tests/test_alpha_vantage.py`

**Interfaces:**
- Consumes: `SQLiteCache`, optional API key, injected `httpx.Client`, and UTC clock.
- Produces: `AlphaDataset`, `ProviderPayload`, `AlphaVantageClient.get(dataset, symbol, refresh=False) -> ProviderPayload`.
- Guarantees: returned URL never contains `apikey`; stale metadata is explicit.

- [ ] **Step 1: Write cache and provider-envelope tests with `httpx.MockTransport`**

```python
# tests/test_alpha_vantage.py
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from wavebird.alpha_vantage import AlphaDataset, AlphaVantageClient
from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import InvalidTickerError, QuotaError


def test_fresh_cache_prevents_http(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    key = CacheKey("alpha_vantage", "BALANCE_SHEET", "IBM", "BALANCE_SHEET:IBM")
    cache.put(
        key,
        {"symbol": "IBM", "annualReports": [], "quarterlyReports": []},
        now,
        now + timedelta(days=7),
        "https://www.alphavantage.co/query?function=BALANCE_SHEET&symbol=IBM",
    )
    client = AlphaVantageClient(
        cache=cache,
        api_key="demo",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now,
    )
    assert client.get(AlphaDataset.BALANCE_SHEET, "IBM").data["symbol"] == "IBM"
    assert calls == 0


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"Error Message": "Invalid API call"}, InvalidTickerError),
        ({"Note": "25 requests per day"}, QuotaError),
        ({"Information": "rate limit reached"}, QuotaError),
    ],
)
def test_error_envelopes_are_not_success(body, error_type, tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    client = AlphaVantageClient(
        cache=SQLiteCache(tmp_path / "cache.sqlite3"),
        api_key="demo",
        http=httpx.Client(transport=transport),
        now=lambda: now,
    )
    with pytest.raises(error_type):
        client.get(AlphaDataset.CASH_FLOW, "BAD")
```

Also test: missing key on cache miss, sanitized source URL, API key absent from exception text, refresh bypass, HTTP status mapping, expired stale fallback on `httpx.TimeoutException`, expired stale fallback on quota, and no stale fallback on invalid ticker/schema/authentication/other 4xx errors.

- [ ] **Step 2: Run the focused tests and observe import failure**

Run: `uv run pytest tests/test_alpha_vantage.py -q`

Expected: FAIL because `wavebird.alpha_vantage` does not exist.

- [ ] **Step 3: Implement datasets and TTLs**

```python
class AlphaDataset(StrEnum):
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"
    BALANCE_SHEET = "BALANCE_SHEET"
    EARNINGS = "EARNINGS"
    WEEKLY_ADJUSTED = "TIME_SERIES_WEEKLY_ADJUSTED"


TTL_BY_DATASET = {
    AlphaDataset.INCOME_STATEMENT: timedelta(days=7),
    AlphaDataset.CASH_FLOW: timedelta(days=7),
    AlphaDataset.BALANCE_SHEET: timedelta(days=7),
    AlphaDataset.EARNINGS: timedelta(days=7),
    AlphaDataset.WEEKLY_ADJUSTED: timedelta(days=1),
}
```

`ProviderPayload` is an immutable Pydantic model with `data: dict[str, Any]` and `source: SourceMetadata`.

- [ ] **Step 4: Implement request and stale-fallback policy**

`get` performs this exact order:

1. Normalize `symbol = symbol.strip().upper()` and reject non-alphanumeric ticker characters except `.` and `-`.
2. Build `CacheKey("alpha_vantage", dataset.value, symbol, f"{dataset.value}:{symbol}")`.
3. Return a fresh row unless `refresh` is true.
4. If HTTP is needed and no key exists, raise `AuthenticationError`.
5. Call `GET base_url` with `function`, `symbol`, and `apikey`, timeout `10.0` seconds.
6. Convert every `httpx.TransportError` (including timeouts and connection errors) into Wavebird `TransportError`.
7. Map HTTP `401`/`403` to `AuthenticationError`, `429` to `QuotaError`, and `5xx` to `TransportError`. Any other non-2xx response becomes `ProviderResponseError` with the status code; never cache it.
8. Decode a JSON object. Invalid JSON or a non-object root raises `ProviderSchemaError`.
9. Inspect HTTP-200 Alpha envelopes before endpoint validation: `Error Message` is `InvalidTickerError`; `Note` or rate-limit/frequency text in `Information` is `QuotaError`; any other `Information` is `ProviderResponseError`.
10. Validate the root: statement endpoints require `symbol`, `annualReports`, `quarterlyReports`; earnings requires `annualEarnings`, `quarterlyEarnings`; weekly requires `Weekly Adjusted Time Series`.
11. Cache only a valid success payload.
12. On Wavebird `TransportError` or `QuotaError` only, return an expired row with `source.stale=True`; otherwise re-raise.

Create source URLs with `httpx.URL(base_url).copy_add_param("function", ...).copy_add_param("symbol", ...)`; never add the key to stored metadata. Never include `str(httpx_exception)`, the raw request URL, or response bodies in user-facing errors because they can contain the key; build sanitized messages from provider, dataset, symbol, status, and exception type.

- [ ] **Step 5: Run Alpha client tests**

Run: `uv run pytest tests/test_alpha_vantage.py -q`

Expected: PASS with zero real network calls.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/alpha_vantage.py tests/test_alpha_vantage.py
git commit -m "feat: add cache-first Alpha Vantage client"
```

---

### Task 5: Financial Normalization, TTM Construction, and Beta

**Files:**
- Modify: `src/wavebird/alpha_vantage.py`
- Create: `src/wavebird/valuation.py`
- Modify: `tests/test_alpha_vantage.py`
- Create: `tests/test_valuation.py`

**Interfaces:**
- Produces: `parse_financial_periods(payloads, period_kind) -> tuple[FinancialPeriod, ...]`.
- Produces: `build_ttm_periods(quarters) -> tuple[FinancialPeriod, ...]`.
- Produces: `parse_weekly_prices(payload) -> tuple[PricePoint, ...]` and `calculate_beta(stock, spy, as_of) -> float`.
- Consumes: the five Alpha payloads fetched in Task 4.

- [ ] **Step 1: Write normalization tests for date joins, missing values, signs, and reporting dates**

Build compact dictionaries in `tests/test_alpha_vantage.py` with two annual records and five quarterly records. Each statement record uses the same `fiscalDateEnding`; deliberately reverse one endpoint's list to prove joining is by date. Use these exact Alpha field names:

```python
income = {
    "fiscalDateEnding": "2025-12-31",
    "reportedCurrency": "USD",
    "totalRevenue": "1000",
    "ebit": "150",
    "incomeBeforeTax": "130",
    "incomeTaxExpense": "27",
    "interestExpense": "10",
    "netIncome": "103",
}
cash = {
    "fiscalDateEnding": "2025-12-31",
    "capitalExpenditures": "-70",
    "depreciationDepletionAndAmortization": "40",
    "dividendPayoutCommonStock": "20",
}
balance = {
    "fiscalDateEnding": "2025-12-31",
    "reportedCurrency": "USD",
    "totalCurrentAssets": "500",
    "cashAndShortTermInvestments": "100",
    "totalCurrentLiabilities": "300",
    "currentDebt": "50",
    "shortLongTermDebtTotal": "200",
    "totalShareholderEquity": "500",
    "commonStockSharesOutstanding": "20",
}
earnings = {
    "fiscalDateEnding": "2025-12-31",
    "reportedDate": "2026-01-28",
    "reportedEPS": "5.15",
}
```

Assertions: CapEx becomes positive `70`, NWC is `(500-100)-(300-50)=150`, values join despite list order, `"None"` becomes `None`, and `availability_date` uses `reportedDate` before the +45/+90 fallback.

- [ ] **Step 2: Write TTM and beta tests**

```python
# tests/test_valuation.py
from datetime import date

import pytest

from wavebird.models import PricePoint
from wavebird.valuation import calculate_beta


def test_beta_aligns_dates_before_calculation() -> None:
    stock = (
        PricePoint(date=date(2025, 1, 3), close=10, adjusted_close=10),
        PricePoint(date=date(2025, 1, 10), close=12, adjusted_close=12),
        PricePoint(date=date(2025, 1, 17), close=16.8, adjusted_close=16.8),
    )
    spy = (
        PricePoint(date=date(2025, 1, 3), close=100, adjusted_close=100),
        PricePoint(date=date(2025, 1, 10), close=110, adjusted_close=110),
        PricePoint(date=date(2025, 1, 17), close=132, adjusted_close=132),
        PricePoint(date=date(2025, 1, 24), close=145, adjusted_close=145),
    )
    assert calculate_beta(stock, spy, date(2025, 1, 17), min_observations=2) == pytest.approx(2.0)
```

For TTM, use five quarters with revenue `100, 110, 120, 130, 140`, EPS `1, 2, 3, 4, 5`, and NWC `50, 55, 61, 68, 76`. Assert the latest TTM revenue is `500`, EPS is `14`, and delta NWC is `26` (`76-50`). Assert a three-quarter window produces no TTM record.

- [ ] **Step 3: Implement provider parsing helpers**

`parse_number` returns `None` for missing, empty, or literal `"None"`; otherwise it returns a finite float or raises `ProviderSchemaError` with field and fiscal date. Use `abs(value)` for provider CapEx. Reject non-USD joined records.

`parse_financial_periods` indexes all reports by `fiscalDateEnding`. It creates a record only for a date present in income, cash flow, and balance sheet; it never joins by list position. Quarterly records take EPS and `reportedDate` from `quarterlyEarnings`. Annual records take EPS from `annualEarnings` and prefer the matching fiscal-date entry's `reportedDate` from `quarterlyEarnings` (the Q4 release date); if the provider ever supplies an annual `reportedDate`, use it as the second choice. Only then fall back to fiscal date +90 days for annual records or +45 days for quarters. Add an explicit test proving an annual point uses the matching Q4 `reportedDate`. Do not skip a joined record merely because a model-specific field is `None`.

`parse_weekly_prices` reads raw close key `4. close` and adjusted close key `5. adjusted close`, sorts ascending, and rejects non-positive values.

- [ ] **Step 4: Implement TTM construction and beta**

`build_ttm_periods` sorts quarterly records and takes four-quarter windows whose adjacent fiscal dates are 70-110 days apart. Sum only flow fields (`reported_eps`, revenue, EBIT, pretax income, tax expense, interest expense, net income, CapEx, depreciation, and dividends). Copy point-in-time balance fields and shares from the last quarter. Set `delta_nwc = latest.nwc - nwc_from_the_quarter_before_the_window`, which requires a fifth quarter. Its availability date is the latest of the four earnings-release/fallback dates.

`calculate_beta` filters prices at or before `as_of`, keeps the trailing 104 weeks, inner-joins by date, calculates consecutive adjusted-close returns after alignment, requires 52 observations by default, and returns `statistics.covariance(stock_returns, spy_returns) / statistics.variance(spy_returns)`. Reject zero variance and non-finite results with `ValuationMathError`.

- [ ] **Step 5: Run normalization and calculation tests**

Run: `uv run pytest tests/test_alpha_vantage.py tests/test_valuation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/alpha_vantage.py src/wavebird/valuation.py tests/test_alpha_vantage.py tests/test_valuation.py
git commit -m "feat: normalize statements and market returns"
```

---

### Task 6: Damodaran ERP and Treasury Import

**Files:**
- Create: `src/wavebird/damodaran.py`
- Create: `tests/test_damodaran.py`

**Interfaces:**
- Consumes: `SQLiteCache`, injected `httpx.Client`, annual/monthly URLs, and UTC clock.
- Produces: `parse_annual_frame`, `parse_monthly_frame`, and `DamodaranClient.get_rates(as_of, refresh=False) -> RatePoint`.

- [ ] **Step 1: Write frame parsing and schema-drift tests**

```python
# tests/test_damodaran.py
from datetime import date

import pandas as pd
import pytest

from wavebird.damodaran import parse_annual_frame, parse_monthly_frame
from wavebird.errors import ProviderSchemaError


def test_parse_annual_uses_named_damodaran_columns() -> None:
    frame = pd.DataFrame(
        [
            ["metadata", None, None],
            ["Year", "T.Bond Rate", "Implied ERP (FCFE)"],
            [2024, 0.0438, 0.0433],
        ]
    )
    point = parse_annual_frame(frame)[0]
    assert point.date == date(2024, 12, 31)
    assert point.risk_free_rate == 0.0438
    assert point.equity_risk_premium == 0.0433


def test_parse_monthly_converts_excel_serial() -> None:
    frame = pd.DataFrame(
        [
            ["Start of month", "T.Bond Rate", "ERP (T12m)"],
            [45292, 0.0388, 0.0460],
        ]
    )
    assert parse_monthly_frame(frame)[0].date == date(2024, 1, 1)


def test_column_drift_fails_loudly() -> None:
    frame = pd.DataFrame([["Year", "Treasury", "Mystery ERP"], [2024, 0.04, 0.05]])
    with pytest.raises(ProviderSchemaError, match="T.Bond Rate"):
        parse_annual_frame(frame)
```

Also test that monthly values are preferred when available, annual values are used for older dates, no future row is selected, and expired parsed data is used only on transport/quota failure.

- [ ] **Step 2: Run the focused test and observe import failure**

Run: `uv run pytest tests/test_damodaran.py -q`

Expected: FAIL because `wavebird.damodaran` does not exist.

- [ ] **Step 3: Implement deterministic frame parsers**

Use `pandas.read_excel(BytesIO(content), sheet_name="Historical Impl Premiums", header=None, engine="calamine")` for annual data and `sheet_name="Historical ERP"` for monthly data. Find the header row by exact first-column value (`Year` or `Start of month`), then rebuild the DataFrame with that row as columns.

Annual canonical columns are `Year`, `T.Bond Rate`, and `Implied ERP (FCFE)`. Monthly canonical columns are `Start of month`, `T.Bond Rate`, and `ERP (T12m)`. Convert numeric Excel dates with origin `1899-12-30`. Validate every selected rate is finite and between `-1` and `1`.

- [ ] **Step 4: Implement normalized JSON caching and dated selection**

Use cache keys:

```python
ANNUAL_KEY = CacheKey("damodaran", "annual_rates", "", "histimpl")
MONTHLY_KEY = CacheKey("damodaran", "monthly_rates", "", "ERPbymonth")
```

Cache parsed records as JSON dictionaries rather than workbook bytes. Annual TTL is 30 days; monthly TTL is seven days. Apply the same stale policy as Alpha: only transport/quota failures may use expired records, with `SourceMetadata.stale=True`.

`get_rates(as_of)` chooses the latest monthly record with `date <= as_of`; if none exists, it chooses the latest annual record with `date <= as_of`. Raise `DataUnavailableError` if neither source has a past observation.

- [ ] **Step 5: Run rate tests and type checks**

Run: `uv run pytest tests/test_damodaran.py -q && uv run ty check`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/damodaran.py tests/test_damodaran.py
git commit -m "feat: import current Damodaran market rates"
```

---

### Task 7: Stable-growth FCFE and Shared Valuation Guards

**Files:**
- Modify: `src/wavebird/valuation.py`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces: `cost_of_equity`, `require_fields`, `derive_effective_tax_rate`, and `calculate_stable_fcfe`.
- Consumes: `FinancialPeriod`, `MarketInputs`, and `ValuationAssumptions`.

- [ ] **Step 1: Add the exact Damodaran stable-FCFE regression test**

```python
from datetime import date
import pytest

from wavebird.config import ValuationAssumptions
from wavebird.models import MarketInputs
from wavebird.valuation import calculate_stable_fcfe


def test_stable_fcfe_matches_damodaran_fcfest(make_period) -> None:
    period = make_period(reported_eps=5.45, shares_outstanding=1.0)
    market = MarketInputs(
        as_of=date(2025, 12, 31), beta=1.1, risk_free_rate=0.07,
        equity_risk_premium=0.055,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5, high_growth_rate=0.06, stable_growth_rate=0.06,
        stable_roe=0.12, stable_roc=0.12, stable_beta=1.1,
        pretax_cost_of_debt=0.05, target_debt_weight=0.0,
        margin_of_safety=0.25,
    )
    result = calculate_stable_fcfe(period, market, assumptions)
    assert result.assumptions["cost_of_equity"] == pytest.approx(0.1305)
    assert result.value_per_share == pytest.approx(
        40.9716312056738, rel=1e-10, abs=1e-8
    )
```

Add guard tests for missing EPS, non-positive EPS, non-positive shares, non-finite beta, and `cost_of_equity <= stable_growth_rate`.

- [ ] **Step 2: Run the exact test and observe missing function failure**

Run: `uv run pytest tests/test_valuation.py::test_stable_fcfe_matches_damodaran_fcfest -q`

Expected: FAIL because `calculate_stable_fcfe` is undefined.

- [ ] **Step 3: Implement shared guards and formula**

```python
def cost_of_equity(beta: float, risk_free_rate: float, erp: float) -> float:
    value = risk_free_rate + beta * erp
    if not math.isfinite(value):
        raise ValuationMathError("cost of equity is not finite")
    return value


def calculate_stable_fcfe(
    period: FinancialPeriod,
    market: MarketInputs,
    assumptions: ValuationAssumptions,
) -> ModelResult:
    eps, shares = require_fields(period, ModelName.STABLE_FCFE, "reported_eps", "shares_outstanding")
    if eps <= 0 or shares <= 0:
        raise ValuationMathError("stable FCFE requires positive EPS and shares")
    ke = cost_of_equity(market.beta, market.risk_free_rate, market.equity_risk_premium)
    growth = assumptions.stable_growth_rate
    if ke <= growth:
        raise ValuationMathError("stable FCFE requires cost of equity > stable growth")
    fcfe_next = eps * (1 + growth) * (1 - growth / assumptions.stable_roe)
    value = fcfe_next / (ke - growth)
    return valid_result(
        model=ModelName.STABLE_FCFE,
        value_per_share=value,
        shares=shares,
        explicit_stage_pv=0.0,
        terminal_value_pv=value * shares,
        assumptions={"cost_of_equity": ke, "stable_growth_rate": growth,
                     "stable_roe": assumptions.stable_roe},
    )
```

`explicit_stage_pv` and `terminal_value_pv` are always total currency amounts. FCFE functions therefore multiply workbook per-share PVs by shares before constructing the result; FCFF already produces total enterprise PVs. `terminal_value_share` is always `terminal_value_pv / shares`.

Implement the shared helpers exactly:

```python
def require_fields(
    period: FinancialPeriod,
    model: ModelName,
    *names: str,
) -> tuple[float, ...]:
    missing = [name for name in names if getattr(period, name) is None]
    if missing:
        fields = ", ".join(missing)
        raise DataUnavailableError(
            f"{model.value} unavailable for {period.symbol} "
            f"{period.fiscal_date}: missing {fields}"
        )
    return tuple(float(getattr(period, name)) for name in names)


def valid_result(
    *,
    model: ModelName,
    value_per_share: float,
    shares: float,
    explicit_stage_pv: float,
    terminal_value_pv: float,
    projections: tuple[ProjectionPoint, ...] = (),
    assumptions: dict[str, float | int | str],
) -> ModelResult:
    values = (
        value_per_share,
        shares,
        explicit_stage_pv,
        terminal_value_pv,
        *(point.cash_flow for point in projections),
        *(point.present_value for point in projections),
    )
    if shares <= 0 or not all(math.isfinite(value) for value in values):
        raise ValuationMathError("valuation result is non-finite or shares are non-positive")
    return ModelResult(
        model=model,
        value_per_share=value_per_share,
        intrinsic_equity_value=value_per_share * shares,
        explicit_stage_pv=explicit_stage_pv,
        terminal_value_pv=terminal_value_pv,
        terminal_value_share=terminal_value_pv / shares,
        projections=projections,
        assumptions=assumptions,
    )
```

`derive_effective_tax_rate(periods, as_of) -> tuple[float, tuple[date, ...]]` filters to periods available by `as_of`, sorts fiscal dates descending, takes up to three observations with positive pretax income and non-negative tax expense, and returns both the median `tax_expense / pretax_income` and the selected fiscal dates. Reject any derived rate outside `[0, 1)`; if none qualify, raise `DataUnavailableError`. Store the selected dates as an ISO comma-separated `tax_rate_source_periods` result assumption so text and JSON reports disclose them.

- [ ] **Step 4: Run all valuation tests**

Run: `uv run pytest tests/test_valuation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wavebird/valuation.py tests/test_valuation.py
git commit -m "feat: implement stable growth FCFE"
```

---

### Task 8: Two-stage FCFE with Workbook Regression

**Files:**
- Modify: `src/wavebird/valuation.py`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces: `calculate_two_stage_fcfe(period, market, assumptions) -> ModelResult`.
- Consumes: shared guards/results from Task 7.

- [ ] **Step 1: Add the exact `fcfe2st.xls` regression test**

```python
def test_two_stage_fcfe_matches_damodaran_workbook(make_period) -> None:
    period = make_period(
        reported_eps=4.0,
        capex=3.7,
        depreciation=1.7,
        revenue=20.0,
        nwc=8.0,
        shares_outstanding=1.0,
    )
    market = MarketInputs(
        as_of=date(2025, 12, 31), beta=1.3,
        risk_free_rate=0.07, equity_risk_premium=0.055,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.227050969620234,
        stable_growth_rate=0.06,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=1.1,
        pretax_cost_of_debt=0.05,
        target_debt_weight=0.0992555831265509,
        margin_of_safety=0.25,
        capex_growth_rate=0.20,
        depreciation_growth_rate=0.20,
        revenue_growth_rate=0.18,
    )
    result = calculate_two_stage_fcfe(period, market, assumptions)
    assert [p.cash_flow for p in result.projections] == pytest.approx([
        1.44934531768689, 1.89792749443693, 2.47104150291537,
        3.20127887639708, 4.12945483295115,
    ], rel=1e-10, abs=1e-8)
    assert result.assumptions["terminal_cash_flow"] == pytest.approx(5.89724099266713)
    assert result.explicit_stage_pv == pytest.approx(8.40368507114114)
    assert result.terminal_value_pv == pytest.approx(43.1598751388765)
    assert result.value_per_share == pytest.approx(51.5635602100177)
```

Add guard tests for zero revenue, missing CapEx/depreciation/NWC, and stable cost of equity not exceeding stable growth.

- [ ] **Step 2: Run the regression and observe missing function failure**

Run: `uv run pytest tests/test_valuation.py::test_two_stage_fcfe_matches_damodaran_workbook -q`

Expected: FAIL because `calculate_two_stage_fcfe` is undefined.

- [ ] **Step 3: Implement the explicit-stage per-share projection**

For each year `1..forecast_years`, calculate:

```python
eps = eps0 * (1 + assumptions.high_growth_rate) ** year
capex_ps = capex0 / shares * (1 + assumptions.capex_growth_rate) ** year
dep_ps = depreciation0 / shares * (1 + assumptions.depreciation_growth_rate) ** year
revenue_ps = revenue0 / shares * (1 + assumptions.revenue_growth_rate) ** year
previous_revenue_ps = revenue0 / shares * (1 + assumptions.revenue_growth_rate) ** (year - 1)
delta_nwc_ps = (nwc0 / revenue0) * (revenue_ps - previous_revenue_ps)
debt_ratio = assumptions.target_debt_weight
fcfe_ps = eps - (capex_ps - dep_ps) * (1 - debt_ratio) - delta_nwc_ps * (1 - debt_ratio)
present_value = fcfe_ps / (1 + high_stage_ke) ** year
```

Terminal calculations are:

```python
terminal_eps = final_eps * (1 + stable_growth)
terminal_fcfe = terminal_eps * (1 - stable_growth / assumptions.stable_roe)
terminal_ke = cost_of_equity(
    assumptions.stable_beta, market.risk_free_rate, market.equity_risk_premium
)
terminal_value = terminal_fcfe / (terminal_ke - stable_growth)
terminal_pv = terminal_value / (1 + high_stage_ke) ** assumptions.forecast_years
```

Store each explicit year as a per-share `ProjectionPoint`. Sum the per-share explicit and terminal PVs for `value_per_share`; pass `explicit_per_share * shares` and `terminal_pv_per_share * shares` to `valid_result`. Return terminal cash flow, high/stable costs of equity, and all selected/default growth rates in the result assumptions.

- [ ] **Step 4: Run valuation tests**

Run: `uv run pytest tests/test_valuation.py -q`

Expected: PASS, including every workbook intermediate.

- [ ] **Step 5: Commit**

```bash
git add src/wavebird/valuation.py tests/test_valuation.py
git commit -m "feat: implement two stage FCFE"
```

---

### Task 9: Two-stage FCFF with Workbook Regression

**Files:**
- Modify: `src/wavebird/valuation.py`
- Modify: `tests/test_valuation.py`

**Interfaces:**
- Produces: `calculate_two_stage_fcff(period, market, assumptions, tax_rate) -> ModelResult`.
- Consumes: shared guards/results from Task 7.

- [ ] **Step 1: Add the exact `fcff2st.xls` regression test**

```python
def test_two_stage_fcff_matches_damodaran_workbook(make_period) -> None:
    period = make_period(
        ebit=5186.0, capex=2152.0, depreciation=1228.0,
        revenue=16701.0, nwc=3755.0, delta_nwc=499.0,
        cash=500.0, total_debt=1822.0,
        shares_outstanding=993.57,
    )
    market = MarketInputs(
        as_of=date(2025, 12, 31), beta=0.8,
        risk_free_rate=0.053, equity_risk_premium=0.055,
    )
    assumptions = ValuationAssumptions(
        forecast_years=5,
        high_growth_rate=0.105634325588301,
        stable_growth_rate=0.06,
        stable_roe=0.12,
        stable_roc=0.12,
        stable_beta=0.8,
        pretax_cost_of_debt=0.055,
        target_debt_weight=0.0144014503888806,
        margin_of_safety=0.25,
        equity_option_value=1500.0,
    )
    result = calculate_two_stage_fcff(period, market, assumptions, tax_rate=0.2849)
    assert result.assumptions["high_stage_wacc"] == pytest.approx(0.0961694755567984)
    assert [p.cash_flow for p in result.projections] == pytest.approx([
        2681.99139547176, 2965.30174776604, 3278.53939805712,
        3624.86569628555, 4007.77593946084,
    ], rel=1e-10, abs=1e-8)
    assert result.assumptions["terminal_cash_flow"] == pytest.approx(3247.37712358278)
    assert result.assumptions["enterprise_value"] == pytest.approx(69175.1523336163)
    assert result.value_per_share == pytest.approx(66.7825642215609)
```

Add guards for tax outside `[0, 1)`, non-positive revenue/shares, missing EBIT/cash/debt, and `WACC <= stable_growth_rate`.

- [ ] **Step 2: Run the regression and observe missing function failure**

Run: `uv run pytest tests/test_valuation.py::test_two_stage_fcff_matches_damodaran_workbook -q`

Expected: FAIL because `calculate_two_stage_fcff` is undefined.

- [ ] **Step 3: Implement high/stable WACC and FCFF projections**

```python
def wacc(ke: float, kd: float, tax_rate: float, debt_weight: float) -> float:
    return ke * (1 - debt_weight) + kd * (1 - tax_rate) * debt_weight
```

For each explicit year, grow EBIT at `high_growth_rate`, CapEx/depreciation at their configured rates, and revenue at `revenue_growth_rate`. Calculate projected delta NWC from the base NWC/revenue ratio, then calculate NOPAT and FCFF. Discount at high-stage WACC.

For the terminal year, calculate `NOPAT_(n+1) = NOPAT_n * (1 + stable_growth_rate)` and `FCFF_(n+1) = NOPAT_(n+1) * (1 - stable_growth_rate / stable_roc)`. Stable WACC uses `stable_beta` with the same ERP/risk-free rate, pre-tax debt cost, tax rate, and target debt weight. Calculate enterprise value, then:

```python
equity_value = enterprise_value + cash - total_debt - assumptions.equity_option_value
value_per_share = equity_value / shares_outstanding
```

Return enterprise/equity value, high/stable WACC, tax rate, terminal cash flow, and option adjustment in result assumptions.

- [ ] **Step 4: Run all formula tests and static checks**

Run: `uv run pytest tests/test_valuation.py -q && uv run ruff check src/wavebird/valuation.py tests/test_valuation.py && uv run ty check`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wavebird/valuation.py tests/test_valuation.py
git commit -m "feat: implement two stage FCFF"
```

---

### Task 10: Analysis Service and Historical Reconstruction

**Files:**
- Create: `src/wavebird/service.py`
- Create: `tests/test_service.py`
- Modify: `tests/conftest.py`
- Modify: `src/wavebird/models.py`

**Interfaces:**
- Consumes: `AlphaVantageClient`, `DamodaranClient`, assumptions, normalization/beta/tax/model functions.
- Produces: `AnalysisService.analyze(symbol, assumptions, as_of, refresh=False) -> AnalysisReport`.
- Produces: current point, annual points, TTM points, weekly market-cap series, per-model unavailable reasons, and source warnings.

- [ ] **Step 1: Write orchestration tests with fake providers**

Create deterministic fake provider classes in `tests/conftest.py`, not inside `test_service.py`, so reporting and plotting tests can reuse the resulting report. The fake bundle contains three annual periods, nine quarters, at least 60 aligned weekly company/SPY prices with non-zero benchmark variance, and dated rate points. Expose `fake_service` plus:

```python
@pytest.fixture
def analysis_report(fake_service, assumptions) -> AnalysisReport:
    return fake_service.analyze("acme", assumptions, date(2026, 2, 1))
```

Count every provider call. Assert one analysis requests exactly five company datasets, one SPY weekly dataset, and rates for each unique historical availability date plus current `as_of`. With `refresh=True`, assert only the first rate lookup receives `refresh=True`. Assert `as_of` excludes a financial period whose `availability_date` is in the future even when its fiscal date is in the past.

```python
def test_screen_inputs_preserve_each_model_value(fake_service, assumptions) -> None:
    report = fake_service.analyze("acme", assumptions, date(2026, 2, 1))
    assert report.symbol == "ACME"
    assert {result.model for result in report.current.models} == set(ModelName)
    assert len(report.current.models) == 3
    assert report.current.market_price > 0
```

Add tests for: invalid currency rejected; one model missing a required field becomes unavailable while other models remain valid; stale source metadata creates a visible warning; market cap uses raw close and the latest available shares; rates/beta use availability date; annual and TTM histories remain separate; current point is the latest available TTM at or before `as_of`; the nine sensitivity cells per model exist; each center cell equals its base-model value; and a deliberately invalid `discount_rate <= stable_growth_rate` cell stores an unavailable reason rather than clamping either rate.

- [ ] **Step 2: Run the focused test and observe import failure**

Run: `uv run pytest tests/test_service.py -q`
Expected: FAIL because `wavebird.service` does not exist.

- [ ] **Step 3: Implement one fetch bundle and one normalization pass**

`AnalysisService._fetch_bundle` calls Alpha for income, cash flow, balance sheet, earnings, company weekly adjusted, and SPY weekly adjusted. Normalize the symbol once. Pass `refresh` to every provider call. Retain all returned `SourceMetadata` without deduplicating stale states.

- [ ] **Step 4: Implement point construction without look-ahead**

For every eligible annual/TTM period:

1. Require `reported_currency == "USD"`.
2. Use `period.availability_date` as the historical valuation date.
3. Choose the latest company raw close at or before that date.
4. Calculate beta from adjusted company/SPY observations at or before that date.
5. Select Damodaran rates at or before that date. Resolve all unique historical dates plus current `as_of` in sorted order; if `refresh=True`, pass it only to the first rate lookup so the two workbooks are downloaded once, then use the newly cached catalogs for every other date.
6. Derive effective tax rate from the current eligible period plus up to two preceding same-kind periods available by the valuation date; never use a future period. Preserve the selected fiscal dates for reporting. If no valid tax observation exists, mark FCFF and tax-dependent diagnostics unavailable while still running both FCFE models.
7. Run each model independently. Convert only `DataUnavailableError` and `ValuationMathError` into that model's unavailable `ModelResult`; provider/config errors still fail the command.
8. For a valid positive value, calculate discount to value as `1 - market_price / value_per_share`. For a valid zero or negative value, leave discount as `None`; it is economically not a discount and must force `DOES_NOT_PASS`.
9. Build diagnostics with the formulas below. Every comparison uses the prior same-kind year: the immediately prior annual record for annual points, and the TTM record 330-400 days earlier for TTM points. Do not call adjacent overlapping TTM movement year-over-year growth.

If a historical point lacks a prior market close, the minimum beta history, or a past rate observation, omit that point and append a warning naming its kind/date/reason; never invent a market input. The current point is mandatory: missing current price, beta, or rates raises `DataUnavailableError` and fails the command. Model-specific accounting gaps do not omit a point; they produce only that model's unavailable result.

Use these exact diagnostic definitions:

```python
nopat = ebit * (1 - tax_rate)
actual_fcff = nopat - (capex - depreciation) - delta_nwc
actual_fcfe = (
    net_income
    - (capex - depreciation) * (1 - assumptions.target_debt_weight)
    - delta_nwc * (1 - assumptions.target_debt_weight)
)
net_debt = total_debt - cash
debt_to_capital = total_debt / (total_debt + book_equity)
operating_margin = ebit / revenue
roe = net_income / mean((book_equity, prior.book_equity))
invested_capital = book_equity + total_debt - cash
prior_invested_capital = prior.book_equity + prior.total_debt - prior.cash
roc = nopat / mean((invested_capital, prior_invested_capital))
absolute_change = current - prior
growth_rate = current / prior - 1
```

Require every input for a diagnostic independently. Ratios and growth rates are unavailable if their denominator is missing or non-positive; store both `None` and a sibling `<name>_reason` string. Never turn missing inputs into zero. Record EPS, revenue, EBIT, NOPAT, actual FCFE/FCFF, gross debt, cash, net debt, leverage, ROE, ROC, operating margin, and absolute/rate changes for revenue, EBIT, FCFF, and net debt.

For the current point, use the latest TTM period available by `as_of`, but use the latest market close and Damodaran rates at `as_of`. This deliberately distinguishes current price from the period's historical price.

- [ ] **Step 5: Implement current-point sensitivity grids**

Only the current point gets sensitivity data. Use the Cartesian product of these constants for each model:

```python
DISCOUNT_RATE_DELTAS = (-0.01, 0.0, 0.01)
TERMINAL_GROWTH_DELTAS = (-0.005, 0.0, 0.005)
```

For each cell, add the discount-rate delta to a new `MarketInputs` created with `MarketInputs.model_validate({**market.model_dump(), "risk_free_rate": market.risk_free_rate + delta})`; this is an explicit parallel shift to high- and stable-stage discount rates. Likewise create perturbed assumptions with `ValuationAssumptions.model_validate({**assumptions.model_dump(), "stable_growth_rate": assumptions.stable_growth_rate + delta})`. Do not use `model_copy(update=...)`, because Pydantic does not validate copied updates. Rerun the applicable model with all other inputs unchanged. Store a `SensitivityPoint` with the value or the exact validation/math/unavailable reason. Catch Pydantic `ValidationError`, `DataUnavailableError`, and `ValuationMathError` for a cell only. Never clamp a perturbed rate. Assert the `(0.0, 0.0)` cell matches the corresponding base result.

- [ ] **Step 6: Implement market-cap reconstruction and screen state**

Build weekly market cap by carrying forward only shares observations whose availability date is at or before each weekly price date. Do not backfill shares before their first known availability date.

Set screen state exactly:

```python
valid = [result for result in current.models if result.unavailable_reason is None]
if len(valid) != 3:
    screen = ScreenState.INCONCLUSIVE
elif all(
    result.value_per_share is not None
    and result.value_per_share > 0
    and result.discount_to_value is not None
    and result.discount_to_value >= assumptions.margin_of_safety
    for result in valid
):
    screen = ScreenState.PASSES
else:
    screen = ScreenState.DOES_NOT_PASS
```

The financing-assisted indicator is true only when prior revenue, EBIT, and net debt are all positive, net-debt growth exceeds both revenue and EBIT growth, and debt-to-capital increased. Otherwise it is false or unavailable with a reason; never call it causal.

- [ ] **Step 7: Run service tests**

Run: `uv run pytest tests/test_service.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/wavebird/service.py src/wavebird/models.py tests/conftest.py tests/test_service.py
git commit -m "feat: assemble current and historical analysis"
```

---

### Task 11: Deterministic Text and JSON Reporting

**Files:**
- Create: `src/wavebird/reporting.py`
- Create: `tests/test_reporting.py`

**Interfaces:**
- Consumes: `AnalysisReport`.
- Produces: `render_text(report) -> str`, `render_json(report) -> str`.

- [ ] **Step 1: Write exact report-contract tests**

```python
# tests/test_reporting.py
from wavebird.reporting import render_json, render_text


def test_text_report_keeps_models_separate(analysis_report) -> None:
    text = render_text(analysis_report)
    assert "Stable FCFE" in text
    assert "Two-stage FCFE" in text
    assert "Two-stage FCFF" in text
    assert "PASSES SCREEN" in text
    assert "average fair value" not in text.lower()
    assert "not investment advice" in text.lower()


def test_json_round_trips_domain_model(analysis_report) -> None:
    rendered = render_json(analysis_report)
    assert '"screen_state": "PASSES SCREEN"' in rendered
    assert '"terminal_value_share"' in rendered
```

Add tests for stale labels/timestamps, unavailable reasons, explicit versus terminal PV, selected/default assumptions, all three labeled 3-by-3 sensitivity grids, option value zero disclosure, financing-assisted non-causality text, and `INCONCLUSIVE` when any model is unavailable.

- [ ] **Step 2: Run focused tests and observe import failure**

Run: `uv run pytest tests/test_reporting.py -q`

Expected: FAIL because `wavebird.reporting` does not exist.

- [ ] **Step 3: Implement the fixed report sections**

Render sections in this order:

1. Symbol, as-of date, screen state, market price, market capitalization.
2. Three-row comparison table: applicability, value/share, discount/premium, explicit PV/share (`explicit_stage_pv / shares_outstanding`), terminal PV/share, and terminal share of the model's explicit-plus-terminal PV.
3. Model-specific tradeoffs and unavailable reasons.
4. Assumptions: all supplied and resolved default projection rates.
5. One labeled 3-by-3 discount-rate-versus-terminal-growth sensitivity grid per model; show unavailable cells as `N/A`, not zero.
6. Five-year/quarter diagnostic table: EPS, revenue, EBIT, FCFE, FCFF, gross/net debt, leverage, ROE/ROC/margin.
7. Financing-assisted indicator with the sentence `This shows co-movement, not that debt caused growth.`
8. Data provenance: provider/dataset/symbol, fetched/expiry timestamps, source URL, `STALE` state.
9. Historical-reconstruction caveat and `Not investment advice.`

Use these tradeoff statements consistently in text and README:

- Stable FCFE is a steady-state sanity check for mature firms; it is compact but highly sensitive to stable growth and ROE and cannot represent a transition period.
- Two-stage FCFE models the explicit transition in equity cash flow; it exposes CapEx, depreciation, working-capital, and target-debt assumptions but is sensitive to all four.
- Two-stage FCFF is the operating/capital-structure cross-check and is preferable when borrowing distorts equity cash flow; it adds WACC, target-debt, tax, cash/debt, and option-value assumptions.

Use a small internal `format_money`, `format_rate`, and `format_optional`; do not add a table-rendering dependency. JSON is `report.model_dump_json(indent=2)` and therefore preserves every intermediate/result field.

- [ ] **Step 4: Run reporting tests**

Run: `uv run pytest tests/test_reporting.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wavebird/reporting.py tests/test_reporting.py
git commit -m "feat: report comparative valuation results"
```

---

### Task 12: Four-panel Market-cap, DCF, Debt, and Growth Plot

**Files:**
- Create: `src/wavebird/plotting.py`
- Create: `tests/test_plotting.py`

**Interfaces:**
- Consumes: `AnalysisReport` only; plotting never refetches or recalculates valuation data.
- Produces: `plot_report(report, destination: Path) -> Path`.

- [ ] **Step 1: Write structural plot tests using the Agg backend**

```python
# tests/test_plotting.py
from pathlib import Path

from wavebird.plotting import build_figure, plot_report


def test_plot_has_four_named_panels(analysis_report) -> None:
    figure = build_figure(analysis_report)
    assert [axis.get_title() for axis in figure.axes] == [
        "Market capitalization vs intrinsic equity value",
        "Operating performance",
        "Capital structure",
        "Debt and operating growth",
    ]


def test_plot_writes_png(tmp_path: Path, analysis_report) -> None:
    target = tmp_path / "report.png"
    assert plot_report(analysis_report, target) == target
    assert target.stat().st_size > 0
```

Inspect axis line labels and assert all six DCF labels exist: each of Stable FCFE, Two-stage FCFE, and Two-stage FCFF with `(annual)` and `(TTM)`. Assert a missing model point remains `NaN` rather than being interpolated.

- [ ] **Step 2: Run focused tests and observe import failure**

Run: `uv run pytest tests/test_plotting.py -q`

Expected: FAIL because `wavebird.plotting` does not exist.

- [ ] **Step 3: Implement `build_figure` with shared dates and explicit units**

Use `matplotlib.pyplot.subplots(4, 1, sharex=True, figsize=(14, 16), constrained_layout=True)`.

- Panel 1 plots weekly market capitalization as a solid line and annual/TTM intrinsic-equity values as distinct marker/linestyle pairs. Use no interpolation.
- Panel 2 plots annual and TTM revenue, EBIT/NOPAT, and FCFF. Keep currency units; do not introduce an indexed secondary axis.
- Panel 3 plots gross debt, cash, and net debt.
- Panel 4 plots year-over-year revenue, EBIT, FCFF, net-debt growth, and debt-to-capital. Undefined rates are `math.nan` gaps.

Add legends, grid, currency/rate y-axis formatters, and a footer containing `Earnings-release/proxy reconstruction; not a look-ahead-free backtest. Co-movement is not causality.`

`plot_report` creates the destination parent directory, calls `figure.savefig(destination, dpi=150)`, closes the figure, and returns the path.

- [ ] **Step 4: Run plotting tests**

Run: `uv run pytest tests/test_plotting.py -q`

Expected: PASS without a display server.

- [ ] **Step 5: Commit**

```bash
git add src/wavebird/plotting.py tests/test_plotting.py
git commit -m "feat: plot valuation and financing history"
```

---

### Task 13: CLI Commands and Exit Codes

**Files:**
- Create: `src/wavebird/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: settings, cache, both provider clients, `AnalysisService`, renderers, plotter.
- Produces: console entry point `wavebird`; commands `analyze`, `cache refresh`, and `rates refresh`.

- [ ] **Step 1: Write CLI contract tests around `main(argv) -> int`**

```python
# tests/test_cli.py
from pathlib import Path

from wavebird.cli import main
from wavebird.errors import InvalidTickerError


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


def test_analyze_requires_assumptions(capsys) -> None:
    code = main(["analyze", "AAPL"])
    assert code == 2
    assert "--assumptions" in capsys.readouterr().err


def test_domain_error_uses_stable_exit_code(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr("wavebird.cli.build_runtime", lambda settings: FailingRuntime())
    path = tmp_path / "assumptions.toml"
    path.write_text(VALID_ASSUMPTIONS)
    code = main(["analyze", "BAD", "--assumptions", str(path)])
    assert code == 5
    assert "invalid ticker" in capsys.readouterr().err.lower()
```

Add tests for text output, `--json`, `--plot`, `--as-of`, `--refresh`, `cache refresh SYMBOL`, `rates refresh`, lowercase ticker normalization, and no traceback for a `WavebirdError`.

- [ ] **Step 2: Run focused tests and observe import failure**

Run: `uv run pytest tests/test_cli.py -q`

Expected: FAIL because `wavebird.cli` does not exist.

- [ ] **Step 3: Implement argparse surface exactly**

```text
wavebird analyze SYMBOL --assumptions PATH [--as-of YYYY-MM-DD] [--refresh] [--json] [--plot PATH]
wavebird cache refresh SYMBOL
wavebird rates refresh
```

`main(argv: Sequence[str] | None = None) -> int` first wraps `parser.parse_args(argv)` in `try/except SystemExit` and returns `int(error.code)`. This makes direct tests and the installed console wrapper agree: help returns `0`, syntax errors return `2`, and the generated console wrapper exits with that returned code.

Define a small `Runtime` dataclass containing the shared `SQLiteCache`, `httpx.Client`, both provider clients, and `AnalysisService`. `build_runtime(settings) -> Runtime` wires them once; `Runtime.close()` closes HTTP and SQLite resources. `main` initializes `runtime: Runtime | None = None`, closes it in `finally`, catches `WavebirdError`, prints one line to stderr, and returns `error.exit_code`. Unexpected exceptions remain visible.

A missing API key is allowed when every required Alpha dataset is a fresh cache hit. The client raises authentication only when it actually needs HTTP.

`cache refresh` requests all five company datasets plus SPY weekly with `refresh=True` and prints fetched timestamps. `rates refresh` refreshes both workbooks and prints selected latest dates. `analyze --plot` writes the plot after a successful analysis; `--json` controls stdout only.

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Run the installed console script help**

Run: `uv run wavebird --help`

Expected: exit 0 and list `analyze`, `cache`, and `rates`.

- [ ] **Step 6: Commit**

```bash
git add src/wavebird/cli.py tests/test_cli.py
git commit -m "feat: expose Wavebird command line interface"
```

---

### Task 14: Documentation, Clean Cutover, CI, and Live Smoke

**Files:**
- Create: `examples/assumptions.toml`
- Create: `.github/workflows/ci.yml`
- Modify: `readme.md`
- Delete: `main.py`
- Delete: `alpha.py`
- Delete: `discount.py`
- Delete: `requirements.txt`
- Delete: `HistoricalRates.csv`
- Delete: `weekly.json`
- Delete: `table.json`
- Delete: `fig.png`
- Delete: `examples/EDGAR_DATA.json`
- Delete: `examples/INCOME_STATEMENT.json`
- Delete: `examples/CASH_FLOW.json`
- Delete: `examples/BALANCE_SHEET.json`
- Delete: `junk/edgarscraper.py`
- Delete: `junk/cik_ticker.csv`
- Delete: tracked files under `__pycache__/`

**Interfaces:**
- Consumes: the complete installed CLI.
- Produces: reproducible user workflow, clean repository, Python-version matrix, and live/cached smoke evidence.

- [ ] **Step 1: Create the explicit example assumptions file**

```toml
# examples/assumptions.toml
forecast_years = 5
high_growth_rate = 0.06
stable_growth_rate = 0.025
stable_roe = 0.12
stable_roc = 0.10
stable_beta = 1.0
pretax_cost_of_debt = 0.05
target_debt_weight = 0.20
margin_of_safety = 0.25
```

The README must say these are examples, not recommendations, and users must supply their own thesis.
- [ ] **Step 2: Replace the README with operational documentation**

Include these sections with executable commands:

1. Requirements and `uv sync --group dev`.
2. `export ALPHAVANTAGE_API_KEY=...`, free-key signup at `https://www.alphavantage.co/support/#api-key`, the documented 25-request/day free-tier constraint, and the six Alpha requests in a completely cold single-symbol analysis (five company datasets including prices plus shared SPY prices).
3. Why Wavebird uses documented direct HTTP endpoints at `https://www.alphavantage.co/documentation/`: the third-party `alpha-vantage` package is unnecessary and does not provide a verified Python 3.12-3.14 compatibility contract.
4. Assumptions-file field table with decimal-rate examples.
5. `uv run wavebird analyze IBM --assumptions examples/assumptions.toml --plot ibm.png`.
6. Stable FCFE, two-stage FCFE, and two-stage FCFF formulas and tradeoffs.
7. Conservative all-three-model screen rule; no averaged value or recommendation.
8. SQLite location, TTLs, refresh behavior, and stale labels.
9. Exact Damodaran annual/monthly workbook URLs and the Alpha Vantage documentation URL, with source acknowledgement.
10. Annual/TTM history, matching-Q4 earnings-release date/fallback timing, and restatement/look-ahead warning.
11. Debt/growth panel's non-causal interpretation.
12. Supported mature USD-reporting US-listed-company scope and excluded firm types.
13. Verification commands.

- [ ] **Step 3: Add the Python-version CI matrix**

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python: ["3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ matrix.python }}
      - run: uv sync --locked --group dev
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run ty check
      - run: uv run pytest
```

- [ ] **Step 4: Remove the legacy implementation and generated artifacts**

Run the exact tracked-file deletion:

```bash
git rm main.py alpha.py discount.py requirements.txt HistoricalRates.csv \
  weekly.json table.json fig.png \
  examples/EDGAR_DATA.json examples/INCOME_STATEMENT.json \
  examples/CASH_FLOW.json examples/BALANCE_SHEET.json \
  junk/edgarscraper.py junk/cik_ticker.csv \
  __pycache__/alpha.cpython-38.pyc __pycache__/alpha.cpython-39.pyc \
  __pycache__/discount.cpython-38.pyc __pycache__/discount.cpython-39.pyc
```

Remove now-empty `junk/` and `__pycache__/` directories from the filesystem. Do not retain aliases or compatibility wrappers.

- [ ] **Step 5: Run every offline quality gate**

Run:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

Expected: every command exits 0.

- [ ] **Step 6: Run the live IBM cold-cache smoke**

Use an isolated cache and the official demo key:

```bash
rm -f /tmp/wavebird-smoke.sqlite3
ALPHAVANTAGE_API_KEY=demo \
WAVEBIRD_CACHE_PATH=/tmp/wavebird-smoke.sqlite3 \
uv run wavebird analyze IBM \
  --assumptions examples/assumptions.toml \
  --plot /tmp/wavebird-ibm.png
```

Expected: exit 0; all three model rows are printed or carry exact model-specific unavailability reasons; source timestamps appear; `/tmp/wavebird-ibm.png` is non-empty. If the demo key is quota-limited at execution time, preserve the offline test evidence and report the external quota response verbatim rather than weakening the smoke path.

- [ ] **Step 7: Prove the warm run performs no provider HTTP**

Run with the same cache but unreachable provider URLs:

```bash
WAVEBIRD_CACHE_PATH=/tmp/wavebird-smoke.sqlite3 \
WAVEBIRD_ALPHA_BASE_URL=http://127.0.0.1:9/query \
WAVEBIRD_DAMODARAN_ANNUAL_URL=http://127.0.0.1:9/annual.xls \
WAVEBIRD_DAMODARAN_MONTHLY_URL=http://127.0.0.1:9/monthly.xlsx \
uv run wavebird analyze IBM --assumptions examples/assumptions.toml
```

Expected: exit 0 from fresh SQLite entries. A connection attempt would fail, so success proves the cache-first path.

- [ ] **Step 8: Run the suite on all supported interpreters**

Run:

```bash
uv run --python 3.12 pytest
uv run --python 3.13 pytest
uv run --python 3.14 pytest
```

Expected: each exits 0. If an interpreter is absent, install it with `uv python install 3.12 3.13 3.14` and rerun the same commands.

- [ ] **Step 9: Commit the clean cutover**

```bash
git add readme.md examples/assumptions.toml .github/workflows/ci.yml .gitignore uv.lock
git add -u
git commit -m "chore: complete Wavebird modernization cutover"
```

- [ ] **Step 10: Inspect final repository state**

Run: `git status --short`

Expected: no output. Confirm the final commit contains no API key, SQLite database, smoke image, or `/tmp` artifact.
