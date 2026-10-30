from datetime import date, timedelta

import httpx
import pytest

from wavebird.cache import CacheKey, SQLiteCache
from wavebird.errors import ProviderSchemaError, TransportError
from wavebird.shiller import (
    SHILLER_KEY,
    SHILLER_TTL,
    SHILLER_URL,
    ShillerClient,
    parse_shiller_html,
)

HTML = """
<html><body><table>
<tr><th>Date</th><th>Value</th></tr>
<tr><td>Oct 1, 2026</td><td>&#x2002; 40.81</td></tr>
<tr><td>Sep 1, 2026</td><td>&nbsp;39.90</td></tr>
<tr><td>Aug 1, 2026</td><td> 38.4 </td></tr>
</table></body></html>
"""


def test_parse_shiller_html_orders_records_ascending() -> None:
    records = parse_shiller_html(HTML)
    assert [(record.date, record.value) for record in records] == [
        (date(2026, 8, 1), 38.4),
        (date(2026, 9, 1), 39.9),
        (date(2026, 10, 1), 40.81),
    ]


def test_parse_shiller_html_rejects_empty_table() -> None:
    with pytest.raises(ProviderSchemaError):
        parse_shiller_html("<html><table><tr><th>Date</th></tr></table></html>")


def make_client(cache: SQLiteCache | None, handler) -> ShillerClient:
    return ShillerClient(
        cache=cache,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        url=SHILLER_URL,
    )


def test_client_fetches_and_caches_series(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=HTML)

    client = make_client(cache, handler)
    fetch = client.get_series()
    assert len(fetch.records) == 3
    assert len(requests) == 1

    cached = cache.get(CacheKey("multpl", "shiller_pe", "", "shiller_pe"))
    assert cached is not None

    # Fresh cache entry: second call must not hit the network.
    again = client.get_series()
    assert len(again.records) == 3
    assert len(requests) == 1
    cache.close()


def test_client_refresh_bypasses_fresh_cache(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=HTML)

    client = make_client(cache, handler)
    client.get_series()
    client.get_series(refresh=True)
    assert len(requests) == 2
    cache.close()


def test_client_falls_back_to_stale_cache_on_transport_error(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    client = make_client(cache, lambda request: httpx.Response(200, text=HTML))
    client.get_series()
    record = cache.get(CacheKey("multpl", "shiller_pe", "", "shiller_pe"))
    assert record is not None
    cache.put(
        SHILLER_KEY,
        record.payload,
        record.fetched_at - timedelta(days=31),
        record.fetched_at - timedelta(days=1),
        SHILLER_URL,
    )

    failing = make_client(cache, lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("x")))
    stale = failing.get_series()
    assert len(stale.records) == 3
    assert stale.source is not None and stale.source.stale is True
    cache.close()


def test_client_raises_without_cache_on_http_error(tmp_path) -> None:
    client = make_client(None, lambda request: httpx.Response(500))
    with pytest.raises(TransportError):
        client.get_series()


def test_ttl_is_monthly() -> None:
    assert timedelta(days=30) == SHILLER_TTL
