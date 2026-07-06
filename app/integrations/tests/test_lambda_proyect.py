"""Tests for app.integrations.lambda_proyect (Contrato B REST client, Fase 6).

Both public methods must be fail-open: an HTTP error is logged and swallowed,
never raised, so a VOICE-integration hiccup can never break the WA payment flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.integrations.lambda_proyect import LambdaProyectClient


@pytest.fixture()
def mock_http() -> AsyncMock:
    return AsyncMock()


async def test_escalate_case_posts_expected_body(mock_http: AsyncMock) -> None:
    response = httpx.Response(200, json={"case_id": "c1", "status": "escalated"})
    mock_http.post.return_value = response
    client = LambdaProyectClient(http=mock_http)

    await client.escalate_case("c1", reason="cartera_rejected", note="Comprobante ilegible")

    mock_http.post.assert_awaited_once_with(
        "/cobranza/case/c1/escalate",
        json={"reason": "cartera_rejected", "channel": "whatsapp", "note": "Comprobante ilegible"},
    )


async def test_escalate_case_swallows_http_error(mock_http: AsyncMock) -> None:
    mock_http.post.side_effect = httpx.ConnectError("boom")
    client = LambdaProyectClient(http=mock_http)

    await client.escalate_case("c1", reason="cartera_rejected")  # must not raise


async def test_escalate_case_swallows_4xx(mock_http: AsyncMock) -> None:
    request = httpx.Request("POST", "http://test/cobranza/case/c1/escalate")
    mock_http.post.return_value = httpx.Response(404, request=request)
    client = LambdaProyectClient(http=mock_http)

    await client.escalate_case("c1", reason="cartera_rejected")  # must not raise


async def test_update_debtor_posts_expected_fields(mock_http: AsyncMock) -> None:
    response = httpx.Response(200, json={"debtor_id": "d1", "updated": True})
    mock_http.post.return_value = response
    client = LambdaProyectClient(http=mock_http)

    await client.update_debtor(
        "d1", estado="pagado", ultima_interaccion_wa="2026-07-05T00:00:00+00:00"
    )

    mock_http.post.assert_awaited_once_with(
        "/cobranza/debtor/d1/update",
        json={"estado": "pagado", "ultima_interaccion_wa": "2026-07-05T00:00:00+00:00"},
    )


async def test_update_debtor_swallows_http_error(mock_http: AsyncMock) -> None:
    mock_http.post.side_effect = httpx.ConnectError("boom")
    client = LambdaProyectClient(http=mock_http)

    await client.update_debtor("d1", estado="pagado")  # must not raise
