"""Respuestas frecuentes determinísticas (common responses).

Preguntas sacadas de las conversaciones reales de Chatwoot (sep-2026). Se
consultan ANTES del LLM en ``node_identify`` (donde no hay LLM y el bot solo
pedía cédula) y en ``node_answer``; además se inyectan al system prompt para
que el modelo las use en vez de escalar.

``escalate=True``: la respuesta explica y además pasa a un humano (link de
pago, recibos, siniestros): el bot no puede resolverlo solo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Faq:
    key: str
    pattern: re.Pattern[str]
    answer: str
    escalate: bool = False


_SIN_AL_DIA = (
    "Gracias por avisarnos. Para que cartera lo verifique y actualice tu póliza, "
    "envíame por este chat el comprobante (foto o PDF). Los pagos pueden tardar "
    "un poco en verse reflejados en el sistema; el equipo de cartera te confirma."
)

FAQS: tuple[Faq, ...] = (
    Faq(
        "quien_habla",
        re.compile(
            r"qui[eé]n (me )?(habla|eres|es)|qu[eé] empresa|de d[oó]nde (me )?(llaman|escriben)",
            re.I,
        ),
        "Soy ARIA, el asistente virtual de DPG Seguros, la agencia que administra tu "
        "póliza (la aseguradora es otra compañía, por ejemplo Sura o Mapfre). Te "
        "escribimos por la gestión de cartera de tu seguro.",
    ),
    Faq(
        "por_que_cobranza",
        re.compile(
            r"por ?qu[eé] (cobranza|me (llaman|escriben|contactan))|qu[eé] me est[aá]n ofreciendo",
            re.I,
        ),
        "Te contactamos desde cartera de DPG Seguros porque tu póliza tiene una cuota "
        "pendiente según nuestro sistema. Si ya la pagaste, envíame el comprobante por "
        "este chat y cartera lo verifica.",
    ),
    Faq(
        "ya_pague",
        re.compile(
            r"ya (lo |la |las |se )?(pagu[eé]|pague|cancel[eé]|pag[oó]|cancel[oó])|hice el pago"
            r"|est(oy|á) al d[ií]a|qued[oó] pag",
            re.I,
        ),
        _SIN_AL_DIA,
    ),
    Faq(
        "link_pago",
        re.compile(
            r"link|cup[oó]n|pse|d[oó]nde (pago|consigno)|c[oó]mo pago|medios? de pago", re.I
        ),
        "El link o cupón de pago te lo envía directamente el equipo de cartera por este "
        "mismo chat. Ya les paso tu solicitud.",
        escalate=True,
    ),
    Faq(
        "recibo_paz_y_salvo",
        re.compile(
            r"recibo de caja|paz y salvo|car[aá]tula|descargar|enviar(me)? la p[oó]liza"
            r"|copia de (la|mi) p[oó]liza",
            re.I,
        ),
        "Ese documento te lo envía el equipo de cartera de DPG. Ya les paso tu solicitud "
        "para que te lo hagan llegar por este chat.",
        escalate=True,
    ),
    Faq(
        "siniestro",
        re.compile(
            r"siniestro|reclamaci[oó]n|da[ñn]os|terremoto|sismo|ajustador|fisura|afectaci", re.I
        ),
        "Para un siniestro, repórtalo cuanto antes directamente a tu aseguradora con tu "
        "nombre completo, número de documento y número de póliza; ellos asignan el "
        "ajustador. DPG te acompaña en el proceso: te paso con un asesor de siniestros.",
        escalate=True,
    ),
    Faq(
        "deducible",
        re.compile(r"deducible|cu[aá]les son esos costos|cu[aá]nto me cobran", re.I),
        "El deducible y los costos dependen de las condiciones de tu póliza. Ese detalle "
        "te lo confirma un asesor de DPG: te lo paso.",
        escalate=True,
    ),
    Faq(
        "cotizar",
        re.compile(
            r"cotiza|seguros que (uds|ustedes) ofrecen|traspaso|nueva p[oó]liza"
            r"|asegurar (un|mi|otro)",
            re.I,
        ),
        "Las cotizaciones y pólizas nuevas las maneja el equipo comercial de DPG, no "
        "cartera. Te paso con un asesor para que te atienda.",
        escalate=True,
    ),
    Faq(
        "devengada",
        re.compile(r"devengad", re.I),
        "“Devengada” es un estado contable del sistema: significa que la póliza ya fue "
        "emitida y su prima se está causando. No quiere decir vencida ni cancelada; la "
        "vigencia real son las fechas de inicio y fin de la póliza.",
    ),
    Faq(
        "fuera_de_tema",
        re.compile(
            r"system prompt|algoritmo|estructura de datos|cu[aá]nto es \d|presidente|chiste", re.I
        ),
        "Solo puedo ayudarte con tu póliza de DPG Seguros: saldo, estado, coberturas y "
        "pagos. ¿Qué quieres saber de tu póliza?",
    ),
)

_DOC_RE = re.compile(r"\d[\d.\- ]{4,}")


def match_faq(text: str) -> Faq | None:
    """Primera FAQ cuyo patrón aparece en ``text``; None si parece un documento."""
    if not text or _DOC_RE.search(text):
        return None
    return next((f for f in FAQS if f.pattern.search(text)), None)


def faq_prompt_section() -> str:
    """Bloque para el system prompt: el LLM las usa antes de escalar."""
    lines = [
        "RESPUESTAS FRECUENTES (úsalas, adaptando el tono, antes de decir que no sabes o"
        " escalar; si la entrada dice 'pasar a un asesor', llama escalate_to_human):"
    ]
    for f in FAQS:
        lines.append(f"- [{f.key}{', pasar a un asesor' if f.escalate else ''}] {f.answer}")
    return "\n".join(lines)
