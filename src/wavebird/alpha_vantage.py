import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import (
    AuthenticationError,
    InvalidTickerError,
    ProviderResponseError,
    ProviderSchemaError,
    QuotaError,
    TransportError,
)
from wavebird.models import SourceMetadata

DEFAULT_BASE_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT_SECONDS = 10.0
DAILY_REQUEST_LIMIT = 25  # documented free-tier allowance
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\-]+$")

_RATE_LIMIT_MARKERS = ("rate limit", "frequency", "25 requests", "premium")


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

# The index series moves ~1 point per week and is shared by every analysis, so it
# is worth refetching only weekly.
SPY_SYMBOL = "SPY"
SPY_WEEKLY_TTL = timedelta(days=7)

_STATEMENT_DATASETS = frozenset(
    {
        AlphaDataset.INCOME_STATEMENT,
        AlphaDataset.CASH_FLOW,
        AlphaDataset.BALANCE_SHEET,
    }
)


class ProviderPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: dict[str, Any]
    source: SourceMetadata


def _required_root_keys(dataset: AlphaDataset) -> tuple[str, ...]:
    if dataset in _STATEMENT_DATASETS:
        return ("symbol", "annualReports", "quarterlyReports")
    if dataset is AlphaDataset.EARNINGS:
        return ("annualEarnings", "quarterlyEarnings")
    return ("Weekly Adjusted Time Series",)


class AlphaVantageClient:
    def __init__(
        self,
        cache: SQLiteCache | None,
        api_key: str | None,
        http: httpx.Client,
        now: Callable[[], datetime] | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.cache = cache
        self.api_key = api_key
        self.http = http
        self.base_url = base_url
        self.now = now if now is not None else (lambda: datetime.now(UTC))

    def get(
        self,
        dataset: AlphaDataset,
        symbol: str,
        refresh: bool = False,
    ) -> ProviderPayload:
        normalized = symbol.strip().upper()
        if not normalized or not SYMBOL_PATTERN.fullmatch(normalized):
            raise InvalidTickerError(
                f"invalid ticker {symbol!r}: only letters, digits, '.', and '-' are allowed"
            )
        key = CacheKey("alpha_vantage", dataset.value, normalized, f"{dataset.value}:{normalized}")

        if not refresh and self.cache is not None:
            record = self.cache.get(key)
            if record is not None and record.is_fresh(self.now()):
                return self._payload_from_record(dataset, normalized, record, stale=False)

        if self.api_key is None:
            raise AuthenticationError(
                "Alpha Vantage API key is required for "
                f"{dataset.value} ({normalized}); set ALPHAVANTAGE_API_KEY"
            )

        try:
            payload = self._fetch(dataset, normalized)
        except (TransportError, QuotaError):
            if self.cache is not None:
                record = self.cache.get(key)
                if record is not None:
                    return self._payload_from_record(dataset, normalized, record, stale=True)
            raise
        return payload

    def _fetch(self, dataset: AlphaDataset, symbol: str) -> ProviderPayload:
        try:
            response = self.http.get(
                self.base_url,
                params={"function": dataset.value, "symbol": symbol, "apikey": self.api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as error:
            raise TransportError(
                f"Alpha Vantage {dataset.value} request for {symbol} failed: {type(error).__name__}"
            ) from error

        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"Alpha Vantage rejected the API key for {dataset.value} ({symbol}); "
                f"HTTP {response.status_code}"
            )
        if response.status_code == 429:
            raise QuotaError(
                f"Alpha Vantage quota exceeded for {dataset.value} ({symbol}); HTTP 429"
            )
        if response.status_code >= 500:
            raise TransportError(
                f"Alpha Vantage server error for {dataset.value} ({symbol}); "
                f"HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderResponseError(
                f"Alpha Vantage returned HTTP {response.status_code} for {dataset.value} ({symbol})"
            )

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise ProviderSchemaError(
                f"Alpha Vantage {dataset.value} response for {symbol} is not valid JSON"
            ) from error
        if not isinstance(body, dict):
            raise ProviderSchemaError(
                f"Alpha Vantage {dataset.value} response for {symbol} is not a JSON object"
            )
        if self.cache is not None:
            self.cache.record_request("alpha_vantage", self.now())

        if "Error Message" in body:
            raise InvalidTickerError(
                f"Alpha Vantage rejected ticker {symbol} for {dataset.value}: "
                f"{body['Error Message']}"
            )
        if "Note" in body:
            raise QuotaError(f"Alpha Vantage rate limit reached for {dataset.value} ({symbol})")
        if "Information" in body:
            information = str(body["Information"])
            lowered = information.lower()
            if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
                raise QuotaError(f"Alpha Vantage rate limit reached for {dataset.value} ({symbol})")
            raise ProviderResponseError(
                f"Alpha Vantage informational response for {dataset.value} ({symbol})"
            )

        missing = [name for name in _required_root_keys(dataset) if name not in body]
        if missing:
            raise ProviderSchemaError(
                f"Alpha Vantage {dataset.value} response for {symbol} is missing "
                f"required keys: {', '.join(missing)}"
            )

        now = self.now()
        source_url = str(
            httpx.URL(self.base_url)
            .copy_add_param("function", dataset.value)
            .copy_add_param("symbol", symbol)
        )
        source = SourceMetadata(
            provider="alpha_vantage",
            dataset=dataset.value,
            symbol=symbol,
            source_url=source_url,
            fetched_at=now,
            expires_at=now
            + (
                SPY_WEEKLY_TTL
                if dataset is AlphaDataset.WEEKLY_ADJUSTED and symbol == SPY_SYMBOL
                else TTL_BY_DATASET[dataset]
            ),
            stale=False,
        )
        if self.cache is not None:
            self.cache.put(
                CacheKey("alpha_vantage", dataset.value, symbol, f"{dataset.value}:{symbol}"),
                body,
                now,
                source.expires_at,
                source_url,
            )
        return ProviderPayload(data=body, source=source)

    def _payload_from_record(
        self,
        dataset: AlphaDataset,
        symbol: str,
        record: Any,
        stale: bool,
    ) -> ProviderPayload:
        source = SourceMetadata(
            provider="alpha_vantage",
            dataset=dataset.value,
            symbol=symbol,
            source_url=record.source_url,
            fetched_at=record.fetched_at,
            expires_at=record.expires_at,
            stale=stale,
        )
        return ProviderPayload(data=record.payload, source=source)
