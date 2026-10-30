from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wavebird.models import ModelName, ModelResult, SourceMetadata


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


def test_market_cap_point_requires_market_price() -> None:
    from datetime import date

    from pydantic import ValidationError

    from wavebird.models import MarketCapPoint

    with pytest.raises(ValidationError):
        MarketCapPoint(date=date(2026, 1, 2), market_capitalization=100.0)  # ty: ignore[missing-argument]
    point = MarketCapPoint(date=date(2026, 1, 2), market_price=110.0, market_capitalization=100.0)
    assert point.market_price == 110.0
