import os
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WAVEBIRD_", extra="forbid", populate_by_name=True, env_file=".env"
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
    cache_path: Path = Field(
        default_factory=lambda: (
            Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            / "wavebird"
            / "cache.sqlite3"
        )
    )


class ValuationAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    currency: str = Field(default="USD")
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
        object.__setattr__(self, "currency", self.currency.upper())
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
