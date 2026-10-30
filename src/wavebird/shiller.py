"""Shiller PE (cyclically adjusted P/E) history from multpl.com."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import httpx

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import ProviderSchemaError, QuotaError, TransportError
from wavebird.models import ShillerPoint, SourceMetadata

SHILLER_URL = "https://www.multpl.com/shiller-pe/table/by-month"
SHILLER_KEY = CacheKey(provider="multpl", dataset="shiller_pe", symbol="", request_key="shiller_pe")
SHILLER_TTL = timedelta(days=30)
REQUEST_TIMEOUT_SECONDS = 10.0

_ROW_PATTERN = (
    r"<tr[^>]*>\s*<td[^>]*>\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*</td>"
    r"\s*<td[^>]*>\s*([\d,]+(?:\.\d+)?)\s*</td>"
)


def parse_shiller_html(html: str) -> tuple[ShillerPoint, ...]:
    import html as html_module
    import re

    text = html_module.unescape(html)
    rows = re.findall(_ROW_PATTERN, text, flags=re.IGNORECASE)
    if not rows:
        raise ProviderSchemaError("multpl shiller-pe table contains no monthly rows")
    records: list[ShillerPoint] = []
    for label, raw_value in rows:
        parsed = datetime.strptime(label.strip(), "%b %d, %Y")
        records.append(ShillerPoint(date=parsed.date(), value=float(raw_value.replace(",", ""))))
    records.sort(key=lambda point: point.date)
    return tuple(records)


class ShillerFetch(NamedTuple):
    records: tuple[ShillerPoint, ...]
    source: SourceMetadata | None


class ShillerClient:
    def __init__(
        self,
        cache: SQLiteCache | None,
        http: httpx.Client,
        url: str = SHILLER_URL,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache = cache
        self.http = http
        self.url = url
        self.now = now if now is not None else (lambda: datetime.now(UTC))

    def get_series(self, refresh: bool = False) -> ShillerFetch:
        moment = self.now()
        if not refresh and self.cache is not None:
            record = self.cache.get(SHILLER_KEY)
            if record is not None and record.is_fresh(moment):
                return self._from_record(record, stale=False)

        try:
            payload = self._fetch_and_parse()
        except (TransportError, QuotaError):
            if self.cache is not None:
                cached = self.cache.get(SHILLER_KEY)
                if cached is not None:
                    return self._from_record(cached, stale=True)
            raise

        source = SourceMetadata(
            provider="multpl",
            dataset="shiller_pe",
            symbol="",
            source_url=self.url,
            fetched_at=moment,
            expires_at=moment + SHILLER_TTL,
            stale=False,
        )
        if self.cache is not None:
            self.cache.put(SHILLER_KEY, payload, moment, source.expires_at, self.url)
        records = tuple(ShillerPoint.model_validate(item) for item in payload["records"])
        return ShillerFetch(records=records, source=source)

    def _fetch_and_parse(self) -> dict[str, Any]:
        try:
            response = self.http.get(self.url, timeout=REQUEST_TIMEOUT_SECONDS)
        except httpx.TransportError as error:
            raise TransportError(
                f"multpl shiller-pe download failed: {type(error).__name__}"
            ) from error
        if response.status_code == 429:
            raise QuotaError("multpl shiller-pe rate limited; HTTP 429")
        if response.status_code < 200 or response.status_code >= 300:
            raise TransportError(f"multpl shiller-pe returned HTTP {response.status_code}")
        records = parse_shiller_html(response.text)
        return {"records": [point.model_dump(mode="json") for point in records]}

    def _from_record(self, record: Any, stale: bool) -> ShillerFetch:
        source = SourceMetadata(
            provider="multpl",
            dataset="shiller_pe",
            symbol="",
            source_url=record.source_url,
            fetched_at=record.fetched_at,
            expires_at=record.expires_at,
            stale=stale,
        )
        records = tuple(ShillerPoint.model_validate(item) for item in record.payload["records"])
        return ShillerFetch(records=records, source=source)
