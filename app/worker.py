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


async def mirror_outbound(
    ctx: dict[str, Any],
    *,
    phone: str,
    text: str,
    wamid: str,
    payment_approved: bool = False,
) -> None:
    """Mirror outbound bot reply to Chatwoot API Channel inbox.

    Called after the bot sends a message to the client. Runs asynchronously
    so Chatwoot latency never blocks the bot response.

    Output firewall gate (D-28, T-04-08-03): re-checks check_outbound on the
    text before posting to Chatwoot so a blocked message is never leaked via
    the mirror path. The ``payment_approved`` flag is forwarded from the
    enqueue call in ``_send_outbound``.

    Args:
        ctx: ARQ worker context (not used, required by ARQ protocol).
        phone: Recipient's E.164 phone number (primitive str -- ARQ Pitfall 6).
        text: Bot reply text (primitive str).
        wamid: Meta message ID returned by send_text (primitive str).
        payment_approved: Forwarded from AIMessage.additional_kwargs (D-28).
    """
    import structlog as _structlog

    from app.integrations.chatwoot import get_chatwoot_client
    from app.security.output_firewall import check_outbound

    _log = _structlog.get_logger("worker.mirror_outbound")
    allowed, fw_reason = check_outbound(text, payment_approved=payment_approved)
    if not allowed:
        # Suppressed — the blocked text must not reach Chatwoot (T-04-08-03).
        _log.error(
            "output_firewall.mirror_blocked",
            reason=fw_reason,
            wamid=wamid,
        )
        return

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
    from app.security import audit_log

    # SEC-04: Capture attachment_received audit event before any graph work so
    # every inbound comprobante is recorded even if the graph subsequently fails.
    audit_log.emit_task(
        action="attachment_received",
        actor="worker",
        conversation_id=phone,
        payload={"media_id": media_id, "mime_type": mime_type, "wamid": wamid},
    )

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


async def verify_audit_chain(ctx: dict[str, Any]) -> None:
    """Daily cron (03:00 UTC): verify the full audit log hash chain.

    On mismatch, logs audit_log.chain_tampered at error level so the Sentry
    structlog integration captures it as an alert (ROADMAP Phase 7, key event).

    Fail-open: any exception (DB down, missing table, import error) is logged
    and swallowed -- the worker never crashes on a chain-check failure.
    """
    import structlog as _structlog

    _log = _structlog.get_logger("worker.verify_audit_chain")
    try:
        # Pitfall 5: resolve session_factory from app.state (wired in on_startup).
        from app.main import app as _app
        from app.security.audit_log import verify_chain

        sf = getattr(_app.state, "session_factory", None)
        if sf is None:
            _log.warning("worker.verify_audit_chain.no_session_factory")
            return

        ok, bad_id = await verify_chain(sf)
        if ok:
            _log.info("audit_log.chain_verified")
        else:
            # Error level ensures Sentry capture via structlog/sentry pipeline.
            _log.error("audit_log.chain_tampered", first_bad_id=bad_id)
    except Exception as exc:
        _log.error(
            "worker.verify_audit_chain.error",
            error_type=type(exc).__name__,
        )


async def sink_audit_log(ctx: dict[str, Any]) -> None:
    """Daily cron (03:30 UTC): append new audit rows to the Railway volume NDJSON sink.

    Respects settings.audit.sink_enabled -- returns immediately if False.
    Fail-open: any exception is logged and swallowed.
    """
    import structlog as _structlog

    from app.config.settings import settings as _settings

    _log = _structlog.get_logger("worker.sink_audit_log")

    if not _settings.audit.sink_enabled:
        return

    try:
        from app.main import app as _app
        from app.security.audit_sink import export_audit_ndjson

        sf = getattr(_app.state, "session_factory", None)
        if sf is None:
            _log.warning("worker.sink_audit_log.no_session_factory")
            return

        count = await export_audit_ndjson(sf, _settings.audit.sink_path)
        _log.info("worker.sink_audit_log.done", exported=count)
    except Exception as exc:
        _log.error(
            "worker.sink_audit_log.error",
            error_type=type(exc).__name__,
        )


class WorkerSettings:
    """ARQ worker configuration.

    F3: Chatwoot mirror jobs (incoming + outgoing).
    F4: Payment comprobante async processing (process_attachment).
    F6: Business-hours-aware timer scheduler + 90-day attachment cleanup.
    F5 (DONE, 05-05): verify_audit_chain + sink_audit_log daily audit crons.
    """

    functions: list[Any] = [
        mirror_inbound,
        mirror_outbound,
        process_attachment,
        check_pending_cases,
        cleanup_attachments_90d,
        verify_audit_chain,
        sink_audit_log,
    ]

    # cron_jobs -- four scheduled jobs:
    #   check_pending_cases: every minute during business hours (D-10/D-11/D-12).
    #     minute=set(range(60)) is ARQ's "every minute of every hour" form.
    #     The job itself bails immediately if outside business hours.
    #   cleanup_attachments_90d: daily at 02:00 UTC (21:00 Bogota Sunday).
    #   verify_audit_chain: daily at 03:00 UTC -- off-peak, after cleanup.
    #   sink_audit_log: daily at 03:30 UTC -- after the chain verifier.
    cron_jobs: list[Any] = [
        cron(check_pending_cases, minute=set(range(60))),
        cron(cleanup_attachments_90d, hour={2}, minute={0}),
        cron(verify_audit_chain, hour={3}, minute={0}),
        cron(sink_audit_log, hour={3}, minute={30}),
    ]

    redis_settings: RedisSettings = RedisSettings.from_dsn(settings.redis.url.get_secret_value())

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        """Initialise the DB engine/session_factory and log registered jobs.

        The FastAPI lifespan (which sets ``app.state.session_factory``) does NOT
        run in the ARQ worker process, but payment nodes reach the DB through
        ``_session_factory_fn`` → ``app.main.app.state.session_factory``. Wire it
        here so ``process_attachment`` and the cron jobs can open sessions.
        """
        import structlog

        from app.config.db import create_db_engine, create_session_factory
        from app.main import app as _app

        log = structlog.get_logger("worker.startup")

        engine = create_db_engine()
        _app.state.db_engine = engine
        _app.state.session_factory = create_session_factory(engine)
        ctx["db_engine"] = engine

        log.info(
            "worker.functions.registered",
            count=len(WorkerSettings.functions),
            names=[f.__name__ for f in WorkerSettings.functions],
            db_wired=True,
        )

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        """Dispose the DB engine created in ``on_startup``."""
        engine = ctx.get("db_engine")
        if engine is not None:
            await engine.dispose()
