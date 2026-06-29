"""ARQ worker entrypoint.

Phase 3 wires Chatwoot mirror jobs (mirror_inbound, mirror_outbound) so every
WhatsApp inbound message and every bot outbound reply appears in the Chatwoot
API Channel inbox in real time. Jobs are enqueued by the webhook handler and
execute asynchronously -- never blocking the bot response to the client.

ARQ does NOT auto-read ``REDIS_URL`` -- its ``RedisSettings`` default is
``host='localhost'``. We construct ``RedisSettings`` from the DSN that
``app.config.settings`` already validated, so the worker and the FastAPI
app share the same Redis source-of-truth.

Phase milestones for this file:
  - F1: ``_noop`` placeholder so ``arq app.worker.WorkerSettings`` boots
  - F3: ``mirror_inbound`` + ``mirror_outbound`` (this plan, 03-03)
  - F5: audit log fan-out, rate-limit token resets
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config.settings import settings


async def mirror_inbound(ctx: dict[str, Any], *, phone: str, text: str, wamid: str) -> None:
    """Mirror inbound WhatsApp message to Chatwoot API Channel inbox.

    Called from ``app/webhooks/meta.py`` via ``arq.enqueue_job``. Runs
    asynchronously so Chatwoot latency never blocks the bot response.

    Args:
        ctx: ARQ worker context (not used, required by ARQ protocol).
        phone: Sender's E.164 phone number (primitive str -- ARQ Pitfall 6).
        text: Raw message text (primitive str).
        wamid: Meta message ID for idempotency tracking (primitive str).
    """
    # ponytail: local import keeps cold-start light + avoids circular deps
    from app.integrations.chatwoot import get_chatwoot_client

    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="incoming")


async def mirror_outbound(ctx: dict[str, Any], *, phone: str, text: str, wamid: str) -> None:
    """Mirror outbound bot reply to Chatwoot API Channel inbox.

    Called after the bot sends a message to the client. Runs asynchronously
    so Chatwoot latency never blocks the bot response.

    Args:
        ctx: ARQ worker context (not used, required by ARQ protocol).
        phone: Recipient's E.164 phone number (primitive str -- ARQ Pitfall 6).
        text: Bot reply text (primitive str).
        wamid: Meta message ID returned by send_text (primitive str).
    """
    # ponytail: local import keeps cold-start light + avoids circular deps
    from app.integrations.chatwoot import get_chatwoot_client

    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="outgoing")


class WorkerSettings:
    """ARQ worker configuration.

    F3: Chatwoot mirror jobs (incoming + outgoing).
    F5: audit log fan-out, rate-limit token resets.
    """

    functions: list[Any] = [mirror_inbound, mirror_outbound]
    redis_settings: RedisSettings = RedisSettings.from_dsn(settings.redis.url.get_secret_value())

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        """Log registered job names so we can verify the right code is deployed."""
        import structlog

        log = structlog.get_logger("worker.startup")
        log.info(
            "worker.functions.registered",
            count=len(WorkerSettings.functions),
            names=[f.__name__ for f in WorkerSettings.functions],
        )
