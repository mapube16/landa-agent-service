"""Tests for the agent dashboard HTML render (features/metrics/dashboard.py)."""

from __future__ import annotations

from app.features.metrics.dashboard import render_dashboard


def test_render_shows_operational_metrics() -> None:
    html = render_dashboard(
        {
            "fecha": "2026-07-29",
            "conversaciones_total": 25,
            "respuestas_a_plantilla": 9,
            "tasa_respuesta_plantilla": 0.36,
            "comprobantes_recibidos": 5,
            "pagos_aprobados": 1,
            "escalaciones": 3,
            "handoffs_de_voz": 2,
            "conv_sin_respuesta": 16,
            "conv_en_conversacion": 4,
            "conv_escaladas": 2,
            "conv_con_comprobante": 2,
            "conv_promesa_pago": 1,
        }
    )
    assert "Operación WhatsApp" in html
    assert "2026-07-29" in html
    assert "36%" in html
    assert ">5<" in html  # comprobantes
    # No audit → no quality block.
    assert "Calidad del asistente" not in html


def test_render_shows_quality_block_when_audited() -> None:
    html = render_dashboard(
        {
            "fecha": "2026-07-29",
            "conversaciones_total": 25,
            "tasa_respuesta_plantilla": 0.36,
            "anom_intento_certificar_pago": 3,
            "anom_lista_rota": 1,
            "anom_loop_sin_salida": 0,
            "anom_cliente_frustrado": 1,
            "conversaciones_auditadas": 8,
        }
    )
    assert "Calidad del asistente" in html
    assert "Intentó certificar un pago" in html
    # A non-zero anomaly is rendered in red (warn).
    assert "#dc2626" in html


def test_render_escapes_content() -> None:
    html = render_dashboard({"fecha": "<script>", "conversaciones_total": 0})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
