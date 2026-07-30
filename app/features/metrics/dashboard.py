"""Server-rendered HTML for the Chatwoot Dashboard App (agent-facing KPIs).

Chatwoot embeds this page as an iframe in the agent panel. To avoid exposing
the metrics bearer in the browser, the numbers are computed server-side and
baked into static HTML — the page ships zero JS that calls a protected API.

Audience is the DPG agent, so labels are plain Spanish. Includes the quality
anomalies (client decision 2026-07-29) under a clearly-separated "calidad del
asistente" block so they aren't confused with operational counts.
"""

from __future__ import annotations

import html
from typing import Any


def _pct(x: float | int | None) -> str:
    return f"{round((x or 0) * 100)}%"


def _row(label: str, value: Any, *, sub: bool = False, warn: bool = False) -> str:
    pad = "padding-left:22px;color:#64748b" if sub else "font-weight:600"
    val_style = "color:#dc2626;font-weight:700" if (warn and value) else "color:#0f172a"
    val = html.escape(str(value))
    return (
        f'<tr><td style="padding:7px 14px;{pad}">{html.escape(label)}</td>'
        f'<td style="padding:7px 14px;text-align:right;{val_style}">{val}</td></tr>'
    )


def render_dashboard(m: dict[str, Any]) -> str:
    """Return the full HTML page for the agent dashboard from a metrics dict."""
    fecha = html.escape(str(m.get("fecha", "")))
    op_rows = "".join(
        [
            _row("Conversaciones del día", m.get("conversaciones_total", 0)),
            _row(
                "Respuestas a la plantilla",
                f"{m.get('respuestas_a_plantilla', 0)}"
                f"  ({_pct(m.get('tasa_respuesta_plantilla'))})",
            ),
            _row("Comprobantes recibidos", m.get("comprobantes_recibidos", 0)),
            _row("Pagos aprobados", m.get("pagos_aprobados", 0)),
            _row("Escalaciones a un humano", m.get("escalaciones", 0)),
            _row("Handoffs desde llamada", m.get("handoffs_de_voz", 0)),
            _row("Sin responder aún", m.get("conv_sin_respuesta", 0), sub=True),
            _row("En conversación con ARIA", m.get("conv_en_conversacion", 0), sub=True),
            _row("Con una persona del equipo", m.get("conv_escaladas", 0), sub=True),
            _row("Con comprobante enviado", m.get("conv_con_comprobante", 0), sub=True),
            _row("Con promesa de pago", m.get("conv_promesa_pago", 0), sub=True),
        ]
    )
    # Quality block only if the audit ran (audited flag present).
    quality_html = ""
    if "conversaciones_auditadas" in m:
        q_rows = "".join(
            [
                _row(
                    "Intentó certificar un pago",
                    m.get("anom_intento_certificar_pago", 0),
                    warn=True,
                ),
                _row("Listas mostradas rotas", m.get("anom_lista_rota", 0), warn=True),
                _row("Se quedó en bucle sin salida", m.get("anom_loop_sin_salida", 0), warn=True),
                _row("Cliente molesto / frustrado", m.get("anom_cliente_frustrado", 0), warn=True),
                _row("Conversaciones revisadas", m.get("conversaciones_auditadas", 0), sub=True),
            ]
        )
        quality_html = (
            '<h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;'
            'color:#334155;margin:22px 0 6px">Calidad del asistente</h3>'
            '<table style="width:100%;border-collapse:collapse;background:#fff;'
            'border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">'
            f"{q_rows}</table>"
        )

    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:0 auto;padding:16px;color:#0f172a">'
        f'<h2 style="font-size:16px;margin:0 0 2px">Operación WhatsApp · ARIA</h2>'
        f'<p style="font-size:12px;color:#64748b;margin:0 0 14px">Día {fecha}</p>'
        '<table style="width:100%;border-collapse:collapse;background:#fff;'
        'border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">'
        f"{op_rows}</table>"
        f"{quality_html}"
        '<p style="font-size:11px;color:#94a3b8;margin-top:14px">'
        "Actualizado al abrir. Datos del canal WhatsApp de DPG Seguros.</p>"
        "</div>"
    )


__all__ = ["render_dashboard"]
