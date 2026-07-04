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
  - F4: ``process_attachment`` payment comprobante processing (04-04)
  - F6: ``check_pending_cases`` + ``cleanup_attachments_90d`` cron jobs (04-06)
  - F5: audit log fan-out, rate-limit token resets

OPERATOR NOTE -- agent-worker Railway service does NOT auto-deploy on git push
(see .planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-06-SMOKE.md
section "Live Smoke Findings"). After merging changes to this file you MUST run:
    railway up --service agent-worker --ci --detach
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.config.settings import settings
from app.features.payment.scheduler import check_pending_cases, cleanup_attachments_90d


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


async def process_attachment(
    ctx: dict[str, Any],
    *,
    phone: str,
    media_id: str,
    mime_type: str,
    wamid: str,
) -> None:
    """Drive the payment graph for an inbound comprobante.

    Enqueued by ``app/webhooks/meta.py`` whenever a client sends an image or
    document. Runs asynchronously so the HTTP webhook returns fast (< 5s Meta
    timeout).

    The job:
    1. Resolves or builds the compiled QA/payment graph from ctx or builds it
       fresh with the shared checkpointer.
    2. Injects ``_inbound_media`` into graph state so ``node_receive_comprobante``
       can pick it up.
    3. Invokes ``graph.ainvoke(None, config)`` which drives:
       receive → forward → awaiting_cartera (which calls interrupt() and persists
       checkpoint). Graph execution suspends at interrupt() and the function
       returns normally; Plan 04-05 resumes via ``aupdate_state``.

    Args:
        ctx: ARQ worker context. May carry ``qa_graph`` and ``checkpointer``
            if the worker lifespan sets them (future phase). Otherwise builds
            graph ad-hoc using the shared checkpointer from settings.
        phone: Sender's E.164 phone (used as LangGraph thread_id).
        media_id: Meta CDN media_id for the comprobante.
        mime_type: Declared MIME type from the webhook.
        wamid: Meta message ID for idempotency.
    """
    # ponytail: local imports keep cold-start light + avoid circular deps.
    from app.config.checkpointer import build_checkpointer_cm
    from app.features.qa.graph import build_qa_graph

    # Try to reuse a graph already compiled in the worker lifespan.
    # If not available (worker deployed before lifespan wires it), build fresh.
    graph = ctx.get("qa_graph")
    if graph is None:
        # Build ad-hoc with a fresh checkpointer context.
        # Note: this opens/closes the checkpointer on every job when no lifespan
        # graph exists. Operator step in Plan 04-06: wire qa_graph into worker
        # on_startup to avoid repeated checkpointer init overhead.
        cp_cm = build_checkpointer_cm()
        checkpointer = await cp_cm.__aenter__()
        try:
            await checkpointer.setup()
            graph = build_qa_graph().compile(checkpointer=checkpointer)
            config: dict[str, Any] = {"configurable": {"thread_id": phone}}
            await graph.aupdate_state(
                config,
                values={
                    "payment_status": "awaiting_receipt",
                    "_inbound_media": {
                        "media_id": media_id,
                        "mime_type": mime_type,
                        "wamid": wamid,
                    },
                    "wa_phone": phone,
                    "thread_id": phone,
                },
                as_node=None,
            )
            await graph.ainvoke(None, config=config)
        finally:
            await cp_cm.__aexit__(None, None, None)
        return

    # Fast path: use pre-compiled graph from worker lifespan.
    config = {"configurable": {"thread_id": phone}}
    await graph.aupdate_state(
        config,
        values={
            "payment_status": "awaiting_receipt",
            "_inbound_media": {
                "media_id": media_id,
                "mime_type": mime_type,
                "wamid": wamid,
            },
            "wa_phone": phone,
            "thread_id": phone,
        },
        as_node=None,
    )
    await graph.ainvoke(None, config=config)


class WorkerSettings:
    """ARQ worker configuration.

    F3: Chatwoot mirror jobs (incoming + outgoing).
    F4: Payment comprobante async processing (process_attachment).
    F6: Business-hours-aware timer scheduler + 90-day attachment cleanup.
    F5: audit log fan-out, rate-limit token resets.
    """

    functions: list[Any] = [
        mirror_inbound,
        mirror_outbound,
        process_attachment,
        check_pending_cases,
        cleanup_attachments_90d,
    ]

    # cron_jobs — two scheduled jobs (Plan 04-06):
    #   check_pending_cases: every minute during business hours (D-10/D-11/D-12).
    #     minute=set(range(60)) is ARQ's "every minute of every hour" form.
    #     The job itself bails immediately if outside business hours, so the
    #     frequent schedule has negligible overhead off-hours.
    #   cleanup_attachments_90d: daily at 02:00 UTC (21:00 Bogota Sunday),
    #     low-traffic window to minimise DB contention (D-02).
    cron_jobs: list[Any] = [
        cron(check_pending_cases, minute=set(range(60))),
        cron(cleanup_attachments_90d, hour={2}, minute={0}),
    ]

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
