import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from wavebird.errors import ConfigurationError

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

REQUEST_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_request_days (
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (provider, day)
) WITHOUT ROWID;
"""

SCHEMA_VERSION = 2


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


class SQLiteCache:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise ConfigurationError(
                f"cache database {path} has unsupported schema version {version}; "
                f"expected at most {SCHEMA_VERSION}"
            )
        with self._connection:
            if version == 0:
                self._connection.execute(SCHEMA)
            self._connection.execute(REQUEST_LOG_SCHEMA)
            if version != SCHEMA_VERSION:
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def get(self, key: CacheKey) -> CacheRecord | None:
        row = self._connection.execute(
            "SELECT payload_json, fetched_at, expires_at, source_url FROM cache_entries "
            "WHERE provider = ? AND dataset = ? AND symbol = ? AND request_key = ?",
            (key.provider, key.dataset, key.symbol, key.request_key),
        ).fetchone()
        if row is None:
            return None
        payload_json, fetched_at, expires_at, source_url = row
        return CacheRecord(
            key=key,
            payload=json.loads(payload_json),
            fetched_at=datetime.fromisoformat(fetched_at),
            expires_at=datetime.fromisoformat(expires_at),
            source_url=source_url,
        )

    def put(
        self,
        key: CacheKey,
        payload: dict[str, Any] | list[Any],
        fetched_at: datetime,
        expires_at: datetime,
        source_url: str,
    ) -> None:
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._connection:
            self._connection.execute(
                "INSERT INTO cache_entries "
                "(provider, dataset, symbol, request_key, payload_json, fetched_at, "
                " expires_at, source_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (provider, dataset, symbol, request_key) DO UPDATE SET "
                "payload_json = excluded.payload_json, "
                "fetched_at = excluded.fetched_at, "
                "expires_at = excluded.expires_at, "
                "source_url = excluded.source_url",
                (
                    key.provider,
                    key.dataset,
                    key.symbol,
                    key.request_key,
                    payload_json,
                    fetched_at.isoformat(),
                    expires_at.isoformat(),
                    source_url,
                ),
            )

    def record_request(self, provider: str, now: datetime | None = None) -> None:
        moment = now if now is not None else datetime.now(UTC)
        day = moment.astimezone(UTC).date().isoformat()
        with self._connection:
            self._connection.execute(
                "INSERT INTO provider_request_days (provider, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT (provider, day) DO UPDATE SET count = count + 1",
                (provider, day),
            )

    def requests_today(self, provider: str, now: datetime | None = None) -> int:
        moment = now if now is not None else datetime.now(UTC)
        day = moment.astimezone(UTC).date().isoformat()
        row = self._connection.execute(
            "SELECT count FROM provider_request_days WHERE provider = ? AND day = ?",
            (provider, day),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def distinct_symbols(self, provider: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT symbol FROM cache_entries WHERE provider = ? ORDER BY symbol",
            (provider,),
        ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        self._connection.close()
