import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Any

import httpx
import pandas as pd
from pydantic import BaseModel, ConfigDict

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import (
    DataUnavailableError,
    ProviderSchemaError,
    QuotaError,
    TransportError,
)
from wavebird.models import RatePoint, SourceMetadata

REQUEST_TIMEOUT_SECONDS = 10.0

ANNUAL_KEY = CacheKey("damodaran", "annual_rates", "", "histimpl")
MONTHLY_KEY = CacheKey("damodaran", "monthly_rates", "", "ERPbymonth")

ANNUAL_SHEET = "Historical Impl Premiums"
MONTHLY_SHEET = "Historical ERP"

ANNUAL_TTL = timedelta(days=30)
MONTHLY_TTL = timedelta(days=7)


class RateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    risk_free_rate: float
    equity_risk_premium: float


def _validate_rate(value: Any, column: str, row_label: str) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if text.lower() == "nan":
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as error:
        raise ProviderSchemaError(
            f"Damodaran column {column!r} at {row_label} is not numeric: {value!r}"
        ) from error
    if percent:
        number = number / 100
    if not math.isfinite(number) or number < -1 or number > 1:
        raise ProviderSchemaError(
            f"Damodaran column {column!r} at {row_label} is out of range: {value!r}"
        )
    return number


class _SkipRow(Exception):
    """Internal signal: a data row is workbook furniture, not a rate record."""


_SKIP_ROW = _SkipRow()


def _records_from_frame(
    frame: pd.DataFrame,
    header_value: str,
    date_column: str,
    risk_free_column: str,
    erp_column: str,
    to_date: Callable[[Any], date],
) -> tuple[RateRecord, ...]:
    first_column = frame.iloc[:, 0]
    header_mask = first_column.astype(str).str.strip() == header_value
    if not header_mask.any():
        raise ProviderSchemaError(f"Damodaran workbook has no {header_value!r} header row")
    header_index = int(header_mask.idxmax())
    columns = [str(name) for name in frame.iloc[header_index].tolist()]
    for required in (date_column, risk_free_column, erp_column):
        if required not in columns:
            raise ProviderSchemaError(
                f"Damodaran workbook is missing required column {required!r}; found {columns}"
            )
    data = frame.iloc[header_index + 1 :].reset_index(drop=True)
    data.columns = columns

    records: list[RateRecord] = []
    for _, row in data.iterrows():
        raw_date = row[date_column]
        if pd.isna(raw_date):
            continue
        try:
            row_date = to_date(raw_date)
        except _SkipRow:
            continue
        risk_free = _validate_rate(row[risk_free_column], risk_free_column, str(raw_date))
        erp = _validate_rate(row[erp_column], erp_column, str(raw_date))
        if risk_free is None or erp is None:
            continue
        records.append(RateRecord(date=row_date, risk_free_rate=risk_free, equity_risk_premium=erp))
    return tuple(records)


def _annual_date(value: Any) -> date:
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise _SKIP_ROW from None
    return date(year, 12, 31)


def _monthly_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date(1899, 12, 30) + timedelta(days=int(text))
    except ValueError:
        parsed = datetime.strptime(text, "%d-%b-%y").date()
        if parsed.year > datetime.now(UTC).year + 1:
            parsed = parsed.replace(year=parsed.year - 100)
        return parsed


def parse_annual_frame(frame: pd.DataFrame) -> tuple[RateRecord, ...]:
    return _records_from_frame(
        frame, "Year", "Year", "T.Bond Rate", "Implied ERP (FCFE)", _annual_date
    )


def parse_monthly_frame(frame: pd.DataFrame) -> tuple[RateRecord, ...]:
    return _records_from_frame(
        frame, "Start of month", "Start of month", "T.Bond Rate", "ERP (T12m)", _monthly_date
    )


class DamodaranClient:
    def __init__(
        self,
        cache: SQLiteCache | None,
        http: httpx.Client,
        annual_url: str,
        monthly_url: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache = cache
        self.http = http
        self.annual_url = annual_url
        self.monthly_url = monthly_url
        self.now = now if now is not None else (lambda: datetime.now(UTC))

    def get_rates(self, as_of: date, refresh: bool = False) -> RatePoint:
        loaded: dict[str, tuple[SourceMetadata, tuple[RateRecord, ...]]] = {}
        errors: list[Exception] = []
        for name, key, url in (
            ("monthly", MONTHLY_KEY, self.monthly_url),
            ("annual", ANNUAL_KEY, self.annual_url),
        ):
            try:
                loaded[name] = self._load_catalog(key, url, refresh)
            except (TransportError, QuotaError) as error:
                errors.append(error)

        monthly = loaded.get("monthly")
        if monthly is not None:
            point = self._select(monthly, as_of, "monthly_rates", self.monthly_url)
            if point is not None:
                return point
        annual = loaded.get("annual")
        if annual is not None:
            point = self._select(annual, as_of, "annual_rates", self.annual_url)
            if point is not None:
                return point
        if errors:
            raise errors[0]
        raise DataUnavailableError(
            f"no Damodaran rate observation at or before {as_of.isoformat()}"
        )

    def _select(
        self,
        catalog: tuple[SourceMetadata, tuple[RateRecord, ...]],
        as_of: date,
        dataset: str,
        url: str,
    ) -> RatePoint | None:
        source, records = catalog
        candidates = [record for record in records if record.date <= as_of]
        if not candidates:
            return None
        latest = max(candidates, key=lambda record: record.date)
        rate_source = SourceMetadata(
            provider="damodaran",
            dataset=dataset,
            symbol="",
            source_url=url,
            fetched_at=source.fetched_at,
            expires_at=source.expires_at,
            stale=source.stale,
        )
        return RatePoint(
            date=latest.date,
            risk_free_rate=latest.risk_free_rate,
            equity_risk_premium=latest.equity_risk_premium,
            source=rate_source,
        )

    def _load_catalog(
        self,
        key: CacheKey,
        url: str,
        refresh: bool,
    ) -> tuple[SourceMetadata, tuple[RateRecord, ...]]:
        now = self.now()
        if not refresh and self.cache is not None:
            record = self.cache.get(key)
            if record is not None and record.is_fresh(now):
                return self._catalog_from_record(record, stale=False)

        ttl = ANNUAL_TTL if key is ANNUAL_KEY else MONTHLY_TTL
        try:
            payload = self._fetch_and_parse(url, key)
        except (TransportError, QuotaError):
            if self.cache is not None:
                cached = self.cache.get(key)
                if cached is not None:
                    return self._catalog_from_record(cached, stale=True)
            raise

        source = SourceMetadata(
            provider="damodaran",
            dataset=key.dataset,
            symbol="",
            source_url=url,
            fetched_at=now,
            expires_at=now + ttl,
            stale=False,
        )
        if self.cache is not None:
            self.cache.put(key, payload, now, source.expires_at, url)
        return source, tuple(RateRecord.model_validate(item) for item in payload["records"])

    def _fetch_and_parse(self, url: str, key: CacheKey) -> dict[str, Any]:
        try:
            response = self.http.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except httpx.TransportError as error:
            raise TransportError(
                f"Damodaran {key.dataset} download failed: {type(error).__name__}"
            ) from error
        if response.status_code >= 500:
            raise TransportError(
                f"Damodaran {key.dataset} server error; HTTP {response.status_code}"
            )
        if response.status_code == 429:
            raise QuotaError(f"Damodaran {key.dataset} rate limited; HTTP 429")
        if response.status_code < 200 or response.status_code >= 300:
            raise TransportError(f"Damodaran {key.dataset} returned HTTP {response.status_code}")
        sheet = ANNUAL_SHEET if key is ANNUAL_KEY else MONTHLY_SHEET
        try:
            frame = pd.read_excel(
                BytesIO(response.content), sheet_name=sheet, header=None, engine="calamine"
            )
        except Exception as error:
            raise ProviderSchemaError(
                f"Damodaran {key.dataset} workbook could not be parsed"
            ) from error
        parser = parse_annual_frame if key is ANNUAL_KEY else parse_monthly_frame
        records = parser(frame)
        if not records:
            raise ProviderSchemaError(f"Damodaran {key.dataset} workbook contains no rate records")
        return {"records": [record.model_dump(mode="json") for record in records]}

    def _catalog_from_record(self, record: Any, stale: bool) -> Any:
        source = SourceMetadata(
            provider="damodaran",
            dataset=record.key.dataset,
            symbol="",
            source_url=record.source_url,
            fetched_at=record.fetched_at,
            expires_at=record.expires_at,
            stale=stale,
        )
        records = tuple(RateRecord.model_validate(item) for item in record.payload["records"])
        return source, records
