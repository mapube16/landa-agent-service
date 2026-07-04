"""RED test for Phase 04 gap closure — GAP 2 (comprobante mirror to Chatwoot).

GAP 2: _handle_comprobante (webhooks/meta.py) must enqueue mirror_inbound after
       successfully enqueuing process_attachment. A human agent in Chatwoot who
       takes over the conversation needs to see the comprobante the client submitted.

       Failure to enqueue mirror_inbound must NOT fail the comprobante path;
       it should only log a warning and continue.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

WEBHOOK_SECRET = "test-webhook-secret-do-not-use-in-prod"
CLIENT_PHONE = "15555550100"  # no '+', as Meta delivers


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    import hashlib
    import hmac as _hmac

    return "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _inbound_image_payload(
    message_id: str = "wamid.img_gap2",
    from_: str = CLIENT_PHONE,
    media_id: str = "MEDIA-GAP2",
    mime_type: str = "image/jpeg",
) -> bytes:
    import json

    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "x",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {},
                                "messages": [
                                    {
                                        "from": from_,
                                        "id": message_id,
                                        "timestamp": "1",
                                        "type": "image",
                                        "image": {
                                            "id": media_id,
                                            "mime_type": mime_type,
                                            "sha256": "abc123",
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


def _inbound_document_payload(
    message_id: str = "wamid.doc_gap2",
    from_: str = CLIENT_PHONE,
    media_id: str = "DOC-GAP2",
    mime_type: str = "application/pdf",
) -> bytes:
    import json

    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "x",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {},
                                "messages": [
                                    {
                                        "from": from_,
                                        "id": message_id,
                                        "timestamp": "1",
                                        "type": "document",
                                        "document": {
                                            "id": media_id,
                                            "mime_type": mime_type,
                                            "filename": "comprobante.pdf",
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


@pytest.fixture()
def stub_app_state_gap2(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Inject mock meta + redis + arq onto app.state for GAP 2 tests."""
    from app.main import app as fastapi_app

    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.out")

    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)  # first-see

    arq_mock = MagicMock()
    arq_mock.enqueue_job = AsyncMock(return_value=None)

    monkeypatch.setattr(fastapi_app.state, "meta", meta_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "redis", redis_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "arq", arq_mock, raising=False)

    return meta_mock, arq_mock


@pytest.mark.asyncio
async def test_comprobante_image_enqueues_mirror_inbound(
    client: AsyncClient,
    stub_app_state_gap2: tuple[MagicMock, MagicMock],
) -> None:
    """GAP 2: inbound image comprobante from allowlisted client must enqueue BOTH
    'process_attachment' AND 'mirror_inbound'.

    mirror_inbound text must include the mime_type so Chatwoot agents can identify
    the file type at a glance.
    """
    meta_mock, arq_mock = stub_app_state_gap2

    body = _inbound_image_payload(
        message_id="wamid.img_gap2_a",
        media_id="MEDIA-IMG-001",
        mime_type="image/jpeg",
    )
    sig = _sign(body)

    r = await client.post(
        "/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    # Let background tasks (create_task) settle.
    await asyncio.sleep(0.05)

    job_names = [c.args[0] for c in arq_mock.enqueue_job.call_args_list if c.args]
    assert (
        "process_attachment" in job_names
    ), f"process_attachment must be enqueued, got jobs: {job_names}"
    assert (
        "mirror_inbound" in job_names
    ), f"mirror_inbound must also be enqueued for comprobante (GAP 2), got jobs: {job_names}"

    # Find the mirror_inbound call and check its kwargs.
    mirror_calls = [
        c for c in arq_mock.enqueue_job.call_args_list if c.args and c.args[0] == "mirror_inbound"
    ]
    assert mirror_calls, "mirror_inbound call not found"
    mirror_kwargs = mirror_calls[0].kwargs
    mirror_phone = mirror_kwargs.get("phone")
    assert mirror_phone in (
        CLIENT_PHONE,
        "+" + CLIENT_PHONE,
    ), f"mirror_inbound phone mismatch: {mirror_kwargs}"
    mirror_text = mirror_kwargs.get("text", "")
    assert (
        "image/jpeg" in mirror_text or "comprobante" in mirror_text.lower()
    ), f"mirror_inbound text must mention mime_type or comprobante, got: {mirror_text!r}"


@pytest.mark.asyncio
async def test_comprobante_pdf_enqueues_mirror_inbound(
    client: AsyncClient,
    stub_app_state_gap2: tuple[MagicMock, MagicMock],
) -> None:
    """GAP 2: inbound PDF document comprobante must also enqueue mirror_inbound."""
    meta_mock, arq_mock = stub_app_state_gap2

    body = _inbound_document_payload(
        message_id="wamid.doc_gap2_b",
        media_id="DOC-PDF-001",
        mime_type="application/pdf",
    )
    sig = _sign(body)

    r = await client.post(
        "/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    await asyncio.sleep(0.05)

    job_names = [c.args[0] for c in arq_mock.enqueue_job.call_args_list if c.args]
    assert (
        "mirror_inbound" in job_names
    ), f"mirror_inbound must be enqueued for PDF comprobante (GAP 2), got: {job_names}"

    mirror_calls = [
        c for c in arq_mock.enqueue_job.call_args_list if c.args and c.args[0] == "mirror_inbound"
    ]
    mirror_text = mirror_calls[0].kwargs.get("text", "")
    assert (
        "application/pdf" in mirror_text or "comprobante" in mirror_text.lower()
    ), f"mirror_inbound text must mention mime_type or comprobante for PDF, got: {mirror_text!r}"


@pytest.mark.asyncio
async def test_mirror_inbound_failure_does_not_fail_comprobante_path(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GAP 2: if mirror_inbound enqueue raises, the comprobante path must NOT fail.
    The webhook must still return 200 and process_attachment must still be enqueued.
    """
    from app.main import app as fastapi_app

    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.out")

    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)

    call_count = {"n": 0}

    async def _enqueue_side_effect(job_name: str, **kwargs: object) -> None:
        call_count["n"] += 1
        if job_name == "mirror_inbound":
            raise RuntimeError("ARQ mirror enqueue failed")

    arq_mock = MagicMock()
    arq_mock.enqueue_job = AsyncMock(side_effect=_enqueue_side_effect)

    monkeypatch.setattr(fastapi_app.state, "meta", meta_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "redis", redis_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "arq", arq_mock, raising=False)

    body = _inbound_image_payload(message_id="wamid.resilience_test")
    sig = _sign(body)

    r = await client.post(
        "/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    # Despite mirror_inbound failing, the webhook must return 200 (not 500).
    assert (
        r.status_code == 200
    ), f"Webhook must return 200 even if mirror_inbound enqueue fails, got {r.status_code}"

    # process_attachment must still have been enqueued before mirror_inbound attempt.
    process_calls = [
        c
        for c in arq_mock.enqueue_job.call_args_list
        if c.args and c.args[0] == "process_attachment"
    ]
    assert process_calls, "process_attachment must still be enqueued even if mirror_inbound fails"
