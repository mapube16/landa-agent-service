"""Tests for the daily metrics computation (features/metrics/daily.py)."""

from __future__ import annotations

from datetime import UTC, date

from app.features.metrics.daily import day_range_utc, summarize


def test_today_co_is_colombia_date() -> None:
    """today_co anchors to UTC-5 so 'hoy' matches the working day, not UTC."""
    from datetime import datetime, timedelta, timezone

    from app.features.metrics.daily import today_co

    expected = datetime.now(timezone(timedelta(hours=-5))).date()
    assert today_co() == expected


def test_day_range_is_colombia_local_in_utc() -> None:
    """A Colombia day starts at 05:00 UTC (UTC-5) and spans 24h."""
    start, end = day_range_utc(date(2026, 7, 29))
    assert start.tzinfo == UTC
    assert start.isoformat() == "2026-07-29T05:00:00+00:00"
    assert end.isoformat() == "2026-07-30T05:00:00+00:00"


def test_summarize_maps_actions_and_labels() -> None:
    action_counts = {
        "attachment_received": 3,
        "escalation": 2,
        "template_button_tap": 5,
        "payment_approved": 1,
    }
    label_counts = {
        "sin-respuesta": 16,
        "en-conversacion": 4,
        "escalado-humano": 2,
        "comprobante-recibido": 2,
        "promesa-pago": 1,
    }
    out = summarize(action_counts, label_counts, total_conversations=25)

    assert out["comprobantes_recibidos"] == 3
    assert out["escalaciones"] == 2
    assert out["pagos_aprobados"] == 1
    assert out["conv_sin_respuesta"] == 16
    assert out["conversaciones_total"] == 25
    # 25 total - 16 sin respuesta = 9 respondieron → 0.36
    assert out["tasa_respuesta_plantilla"] == 0.36


def test_summarize_zero_conversations_no_divide_by_zero() -> None:
    out = summarize({}, {}, total_conversations=0)
    assert out["tasa_respuesta_plantilla"] == 0.0
    assert out["comprobantes_recibidos"] == 0
    assert out["conversaciones_total"] == 0


def test_summarize_missing_keys_default_zero() -> None:
    """Unknown actions/labels are ignored; declared metrics default to 0."""
    out = summarize({"unrelated": 9}, {"otra-label": 3}, total_conversations=1)
    assert out["escalaciones"] == 0
    assert out["conv_escaladas"] == 0
