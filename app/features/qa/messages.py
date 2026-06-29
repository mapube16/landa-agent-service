"""Fixed message templates for the Q&A bot (Phase 3, D-13/D-14/D-16).

Templates T-01..T-08 are DATA, not logic. They are locked per D-16 of
03-CONTEXT.md (copy-exact strings). Zero LLM in the error/escalation
path -- constant cost, zero prompt-injection risk on outbound side.

Tone: Spanish colombiano informal with "tu" (not "vos", not "usted"),
per D-14. Emojis allowed in positive messages (T-01 greeting, T-04
policy list) as an explicit exception to the CLAUDE.md "no emojis"
rule (D-14 override -- warm WhatsApp client tone).

``ESCAPE_REGEX`` is Layer 1 of the hybrid escape hatch (D-15): applied to
raw inbound text before any LLM call. A match triggers immediate transition
to ``escalating`` with T-08, zero LLM cost.

``interpolate_t04`` is the only interpolation helper -- formats the T-04
policy list template. All other templates are ready-to-use strings.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# D-16 locked templates (copy-exact from 03-CONTEXT.md table)
# Emojis in T-01 are intentional per D-14 (warm WhatsApp tone).
# Strings > 100 chars are split into parenthesized implicit concatenation
# to satisfy both ruff-format and black (both configured at line-length=100).
# ---------------------------------------------------------------------------

# Greeting -- asks for document number (D-01 override: documento, not poliza).
T_01: str = (
    "¡Hola! \U0001f44b Soy el asistente virtual de DPG Seguros."
    " Para ayudarte, ¿me das tu número de documento?"
)

T_02: str = (
    "No encontré ese documento en nuestro sistema. ¿Puedes confirmarlo? A veces se cuela un dígito."
)

T_03: str = (
    "Sigo sin encontrar ese documento. Te voy a conectar con un agente de DPG para que te ayude."
)

# T_04 uses str.format() with {N} and {lista_numerada} placeholders.
# Use ``interpolate_t04`` to produce the final string.
T_04: str = (
    "Encontré {N} pólizas a tu nombre:\n\n{lista_numerada}\n\n"
    "¿Sobre cuál querés preguntar? "
    "Respondé con el número o el número de póliza."
)

T_05: str = (
    "Esta póliza no tiene esa información disponible"
    " o está fuera del alcance que puedo consultar."
    " ¿Querés que te conecte con un agente?"
)

T_06: str = (
    "No puedo consultar tu información en este momento."
    " Te voy a conectar con un agente que pueda ayudarte."
)

T_07: str = (
    "Disculpá, no pude armar una respuesta clara a tu pregunta. Te conecto con un agente de DPG."
)

T_08: str = "Listo, te conecto con un agente de DPG. Un humano te va a contestar pronto acá mismo."

# ---------------------------------------------------------------------------
# D-15 Layer 1 -- deterministic escape hatch regex
# ---------------------------------------------------------------------------

# Matches explicit requests for a human agent. Applied to raw inbound text
# (before any LLM call) in EVERY node. A match -> immediate transition to
# ``escalating`` with T-08, zero LLM cost. Case-insensitive; accent
# normalization (NFKC) applied by the caller before matching.
ESCAPE_REGEX: re.Pattern[str] = re.compile(
    r"\b(humano|agente|persona|asesor|representante|hablar con alguien|persona real)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------


def interpolate_t04(n: int, lista_numerada: str) -> str:
    """Return T-04 formatted with the policy count and numbered list.

    Args:
        n: Number of policies found (displayed in the template).
        lista_numerada: Pre-formatted numbered list string, e.g.:
            ``"1. POL-12345 (AUTOMOVILES, Vigente)\\n2. POL-67890 (VIDA, Vigente)"``.
            Caller is responsible for building this string from
            ``PolizaSummary`` objects.

    Returns:
        Ready-to-send WhatsApp message string.
    """
    return T_04.format(N=n, lista_numerada=lista_numerada)


__all__ = [
    "ESCAPE_REGEX",
    "T_01",
    "T_02",
    "T_03",
    "T_04",
    "T_05",
    "T_06",
    "T_07",
    "T_08",
    "interpolate_t04",
]
