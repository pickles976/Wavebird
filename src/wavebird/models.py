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


class ShillerPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    date: date
    value: float = Field(gt=0)


class MarketCapPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    date: date
    market_price: float = Field(gt=0)
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
    shiller_pe: tuple[ShillerPoint, ...] = ()
    sources: tuple[SourceMetadata, ...]
    warnings: tuple[str, ...]
    screen_state: ScreenState
