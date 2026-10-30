import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import ProviderSchemaError, TransportError

REQUEST_TIMEOUT_SECONDS = 15.0
DIRECTORY_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "Wavebird/0.1 (valuation research tool; contact: research@example.com)"
DIRECTORY_TTL = timedelta(days=30)

DIRECTORY_KEY = CacheKey("sec", "ticker_directory", "", "company_tickers")


def parse_ticker_directory(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict) or not payload:
        raise ProviderSchemaError("SEC ticker directory payload is not a non-empty object")
    records: list[dict[str, Any]] = []
    for entry in payload.values():
        try:
            ticker = str(entry["ticker"]).strip().upper()
            title = str(entry["title"]).strip()
            cik = int(entry["cik_str"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderSchemaError(
                "SEC ticker directory entry is missing required fields"
            ) from error
        if not ticker:
            continue
        records.append({"ticker": ticker, "title": title, "cik": cik})
    records.sort(key=lambda record: record["ticker"])
    if not records:
        raise ProviderSchemaError("SEC ticker directory contains no usable entries")
    return tuple(records)


class TickerDirectoryClient:
    def __init__(
        self,
        cache: SQLiteCache | None,
        http: httpx.Client,
        url: str = DIRECTORY_URL,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache = cache
        self.http = http
        self.url = url
        self.now = now if now is not None else (lambda: datetime.now(UTC))

    def load(self, refresh: bool = False) -> tuple[str, ...]:
        now = self.now()
        if not refresh and self.cache is not None:
            record = self.cache.get(DIRECTORY_KEY)
            if record is not None and record.is_fresh(now):
                payload = cast(dict[str, Any], record.payload)
                return tuple(item[0] for item in payload["tickers"])

        try:
            payload = self._fetch()
        except TransportError:
            if self.cache is not None:
                record = self.cache.get(DIRECTORY_KEY)
                if record is not None:
                    payload = cast(dict[str, Any], record.payload)
                    return tuple(item[0] for item in payload["tickers"])
            raise

        if self.cache is not None:
            self.cache.put(DIRECTORY_KEY, payload, now, now + DIRECTORY_TTL, self.url)
        return tuple(item[0] for item in payload["tickers"])

    def _fetch(self) -> dict[str, Any]:
        try:
            response = self.http.get(
                self.url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as error:
            raise TransportError(
                f"SEC ticker directory download failed: {type(error).__name__}"
            ) from error
        if response.status_code < 200 or response.status_code >= 300:
            raise TransportError(f"SEC ticker directory returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise ProviderSchemaError("SEC ticker directory is not valid JSON") from error
        records = parse_ticker_directory(payload)
        return {
            "tickers": [[record["ticker"], record["title"], record["cik"]] for record in records]
        }
