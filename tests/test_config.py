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
