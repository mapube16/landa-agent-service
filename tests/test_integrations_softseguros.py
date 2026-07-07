"""Unit tests for app.integrations.softseguros.

Stubbed httpx + redis; never hits the network. Covers:

- factory cache identity + settings wiring
- token fetch + caching + thundering-herd protection (asyncio.Lock + double-check)
- tenacity transient retry + give-up after 3 attempts
- pybreaker open-state short-circuit (fail-fast)
- Redis read-through cache hit / miss / failure bypass
- 401 → refresh + retry once
- Public read methods route to correct paths
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pybreaker
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset token holder + breaker between tests."""
    from app.integrations import softseguros

    monkeypatch.setattr(softseguros, "_token_holder", {"v": None}, raising=False)
    monkeypatch.setattr(softseguros, "_token_lock", asyncio.Lock(), raising=False)
    try:
        softseguros.softseguros_breaker.close()
    except Exception:
        pass
    yield
    try:
        softseguros.softseguros_breaker.close()
    except Exception:
        pass


@pytest.fixture
def stub_http() -> MagicMock:
    """Stubbed httpx.AsyncClient with AsyncMock for .get and .post."""
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock()
    http.post = AsyncMock()
    return http


@pytest.fixture
def stub_redis() -> MagicMock:
    """Stubbed Redis client; default get → None (cache miss)."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def mocked_client(stub_http: MagicMock, stub_redis: MagicMock) -> Any:
    """SoftSegurosClient with stubbed httpx + redis."""
    from app.integrations.softseguros import SoftSegurosClient

    return SoftSegurosClient(http=stub_http, redis=stub_redis)


def _make_response(status: int, json_body: dict[str, Any] | None = None) -> httpx.Response:
    """Build a real httpx.Response (raise_for_status needs a request bound)."""
    request = httpx.Request("GET", "http://test/x")
    if json_body is not None:
        return httpx.Response(status_code=status, json=json_body, request=request)
    return httpx.Response(status_code=status, request=request)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_get_softseguros_client_is_singleton() -> None:
    from app.integrations.softseguros import get_softseguros_client

    a = get_softseguros_client()
    b = get_softseguros_client()
    assert a is b


def test_get_softseguros_client_uses_settings() -> None:
    from app.integrations.softseguros import get_softseguros_client

    c = get_softseguros_client()
    assert str(c._http.base_url) == "https://app.softseguros.com/"
    assert "landa-agent-service" in c._http.headers["User-Agent"]


# ---------------------------------------------------------------------------
# Token auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_token_caches_in_holder(stub_http: MagicMock) -> None:
    from app.integrations import softseguros

    stub_http.post.return_value = _make_response(200, {"token": "abc123"})

    t1 = await softseguros._get_token(stub_http)
    t2 = await softseguros._get_token(stub_http)

    assert t1 == "abc123"
    assert t2 == "abc123"
    assert stub_http.post.call_count == 1
    assert softseguros._token_holder["v"] == "abc123"


@pytest.mark.asyncio
async def test_get_token_thundering_herd_protection(stub_http: MagicMock) -> None:
    """10 concurrent callers → exactly 1 upstream POST (double-check + lock)."""
    from app.integrations import softseguros

    async def slow_post(*_a: Any, **_kw: Any) -> httpx.Response:
        await asyncio.sleep(0.05)
        return _make_response(200, {"token": "herd-token"})

    stub_http.post.side_effect = slow_post

    tokens = await asyncio.gather(*[softseguros._get_token(stub_http) for _ in range(10)])

    assert tokens == ["herd-token"] * 10
    assert stub_http.post.call_count == 1


# ---------------------------------------------------------------------------
# Tenacity retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retries_on_transient_httpx_errors(
    mocked_client: Any, stub_http: MagicMock
) -> None:
    """ConnectError twice → success on 3rd attempt."""
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.side_effect = [
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        _make_response(200, {"ok": True}),
    ]

    result = await mocked_client._get("/x")

    assert result == {"ok": True}
    assert stub_http.get.call_count == 3


@pytest.mark.asyncio
async def test_get_gives_up_after_3_attempts(mocked_client: Any, stub_http: MagicMock) -> None:
    """tenacity stop_after_attempt(3) → final ConnectError re-raised."""
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.side_effect = [httpx.ConnectError("boom")] * 4

    with pytest.raises(httpx.ConnectError):
        await mocked_client._get("/x")

    assert stub_http.get.call_count == 3


# ---------------------------------------------------------------------------
# Breaker open → fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaker_open_short_circuits_the_call(
    mocked_client: Any, stub_http: MagicMock
) -> None:
    """Forcing the breaker open → CircuitBreakerError; _http.get NEVER called."""
    from app.integrations import softseguros

    softseguros.softseguros_breaker.open()
    try:
        with pytest.raises(pybreaker.CircuitBreakerError):
            await mocked_client._get("/x")
    finally:
        softseguros.softseguros_breaker.close()

    assert stub_http.get.call_count == 0


# ---------------------------------------------------------------------------
# Redis cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_bypasses_upstream(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    stub_redis.get.return_value = b'{"saldo": 100}'

    result = await mocked_client.get_poliza("123")

    assert result == {"saldo": 100}
    assert stub_http.get.call_count == 0


@pytest.mark.asyncio
async def test_cache_miss_writes_back(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.return_value = _make_response(200, {"saldo": 200})

    result = await mocked_client.get_poliza("123")

    assert result == {"saldo": 200}
    stub_redis.set.assert_awaited_once()
    args, kwargs = stub_redis.set.call_args
    assert args[0] == b"softseguros:123:poliza"
    assert json.loads(args[1]) == {"saldo": 200}
    assert kwargs.get("ex") == 60


@pytest.mark.asyncio
async def test_cache_failure_falls_through_to_upstream(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Redis down → log warning, hit upstream, do NOT raise."""
    stub_redis.get.side_effect = ConnectionError("redis down")
    stub_redis.set.side_effect = ConnectionError("redis down")
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.return_value = _make_response(200, {"x": 1})

    result = await mocked_client.get_poliza("123")

    assert result == {"x": 1}


# ---------------------------------------------------------------------------
# 401 → refresh + retry once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_triggers_refresh_and_retry_once(
    mocked_client: Any, stub_http: MagicMock
) -> None:
    stub_http.post.side_effect = [
        _make_response(200, {"token": "old-tok"}),
        _make_response(200, {"token": "new-tok"}),
    ]
    stub_http.get.side_effect = [
        _make_response(401),
        _make_response(200, {"ok": True}),
    ]

    result = await mocked_client._get("/x")

    assert result == {"ok": True}
    assert stub_http.get.call_count == 2
    assert stub_http.post.call_count >= 1


# ---------------------------------------------------------------------------
# Public method path routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,arg,expected_path,expected_params",
    [
        ("get_poliza", "123", "/api/poliza/123/", {}),
        ("get_cliente", "456", "/api/cliente/456/", {}),
        # get_estado now aliases get_poliza — /api/estadopoliza/{id}/ returns 404,
        # estado lives embedded in the poliza object (SOFTSEGUROS_API_NOTES.md).
        ("get_estado", "123", "/api/poliza/123/", {}),
    ],
)
async def test_public_get_methods_route_to_correct_paths(
    mocked_client: Any,
    stub_http: MagicMock,
    stub_redis: MagicMock,
    method: str,
    arg: str,
    expected_path: str,
    expected_params: dict[str, Any],
) -> None:
    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.return_value = _make_response(200, {"ok": True})

    result = await getattr(mocked_client, method)(arg)

    assert result == {"ok": True}
    call_args = stub_http.get.call_args
    assert call_args.args[0] == expected_path
    assert call_args.kwargs.get("params") == expected_params


# ---------------------------------------------------------------------------
# get_clientes_by_documento (Plan 03-02 — D-01 identification by document)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_clientes_by_documento_cache_miss_calls_upstream(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Cache miss → exactly 1 GET with correct path + query param."""
    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.return_value = _make_response(200, {"id": 7, "nombres": "Empresa"})

    result = await mocked_client.get_clientes_by_documento("12345678")

    assert result == {"id": 7, "nombres": "Empresa"}
    assert stub_http.get.call_count == 1
    call_args = stub_http.get.call_args
    assert call_args.args[0] == "/api/cliente/listar_cliente_por_documento/"
    assert call_args.kwargs.get("params") == {"numero_documento": "12345678"}


@pytest.mark.asyncio
async def test_get_clientes_by_documento_cache_hit_skips_upstream(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Cache hit → same ClienteRaw returned; httpx.get never called."""
    cached_payload = {"id": 7, "nombres": "Empresa"}
    stub_redis.get.return_value = json.dumps(cached_payload).encode()

    result = await mocked_client.get_clientes_by_documento("12345678")

    assert result == cached_payload
    assert stub_http.get.call_count == 0


@pytest.mark.asyncio
async def test_get_clientes_by_documento_breaker_open_raises_without_retry(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Open breaker → CircuitBreakerError; no upstream call made."""
    from app.integrations import softseguros

    stub_redis.get.return_value = None
    softseguros.softseguros_breaker.open()
    try:
        with pytest.raises(pybreaker.CircuitBreakerError):
            await mocked_client.get_clientes_by_documento("x")
    finally:
        softseguros.softseguros_breaker.close()

    assert stub_http.get.call_count == 0


def test_allowlist_includes_new_methods() -> None:
    """CI guard allowlist contains both new method names from Plan 03-02."""
    from tests.test_softseguros_readonly import METHOD_ALLOWLIST

    assert "get_clientes_by_documento" in METHOD_ALLOWLIST
    assert "get_polizas_by_cliente" in METHOD_ALLOWLIST


# ---------------------------------------------------------------------------
# get_polizas_by_cliente (Plan 03-02 — two-call pattern second leg)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_polizas_by_cliente_returns_results_list(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Cache miss → unpacks 'results' from paginated DRF response."""
    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    polizas = [{"id": "P1", "numero_poliza": "001"}, {"id": "P2", "numero_poliza": "002"}]
    stub_http.get.return_value = _make_response(
        200, {"count": 2, "next": None, "previous": None, "results": polizas}
    )

    result = await mocked_client.get_polizas_by_cliente(7)

    assert result == polizas
    call_args = stub_http.get.call_args
    assert call_args.args[0] == "/api/poliza/"
    assert call_args.kwargs.get("params") == {"cliente": 7, "limit": 20}


# ---------------------------------------------------------------------------
# get_cartera_status (Fase 6 — replaces the old 504 get_pagos)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cartera_status_returns_first_row_whitelisted(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """Cache miss → routes to the fast endpoint; returns row[0] trimmed to CarteraStatus."""
    from app.models.softseguros import CarteraStatus

    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    raw_row = {
        "fecha_pago": "2026-06-01",
        "fecha_realizara_pago": "2026-07-10",
        "fecha_realizo_pago": None,
        "saldo_pendiente": "150000.00",
        "edad_cartera": 36,
        "ramo_nombre": "AUTOMÓVILES",
        "poliza_codio_objeto_asegurado": "LMT78B",
        "poliza_cliente_celular": "3001234567",  # PII — must NOT leak into CarteraStatus
        "comicion": "12000",  # commission — must NOT leak into CarteraStatus
    }
    stub_http.get.return_value = _make_response(
        200, {"count": 1, "next": None, "previous": None, "results": [raw_row]}
    )

    result = await mocked_client.get_cartera_status("POL123")

    assert result == CarteraStatus(
        fecha_pago="2026-06-01",
        fecha_realizara_pago="2026-07-10",
        fecha_realizo_pago=None,
        saldo_pendiente="150000.00",
        edad_cartera=36,
        ramo_nombre="AUTOMÓVILES",
        riesgo="LMT78B",
    )
    assert not hasattr(result, "poliza_cliente_celular")
    assert not hasattr(result, "comicion")
    call_args = stub_http.get.call_args
    assert call_args.args[0] == "/api/pagopoliza/list_pagospolizas_filtro_paginados/"
    params = call_args.kwargs.get("params")
    assert params["sede"] == 1047
    assert params["texto_busqueda"] == "POL123"
    assert params["search_in"] == "poliza_numero_poliza"
    assert params["tipo"] == "cartera_por_cobrar"


@pytest.mark.asyncio
async def test_get_cartera_status_returns_none_when_no_results(
    mocked_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    """No cartera pendiente (count=0) → None, not an empty CarteraStatus."""
    stub_redis.get.return_value = None
    stub_http.post.return_value = _make_response(200, {"token": "tok"})
    stub_http.get.return_value = _make_response(
        200, {"count": 0, "next": None, "previous": None, "results": []}
    )

    result = await mocked_client.get_cartera_status("POL999")

    assert result is None


def test_allowlist_includes_get_cartera_status() -> None:
    """CI guard allowlist contains the new method name (Fase 6)."""
    from tests.test_softseguros_readonly import METHOD_ALLOWLIST

    assert "get_cartera_status" in METHOD_ALLOWLIST
    assert "get_pagos" not in METHOD_ALLOWLIST
