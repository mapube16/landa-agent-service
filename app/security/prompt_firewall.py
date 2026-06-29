"""Prompt firewall — sanitizes raw inbound text before LLM dispatch.

Pipeline (5 steps, in order):
1. NFKC Unicode normalization  — collapses confusables/variant forms
2. Strip invisible codepoints  — built via chr() calls, NEVER literal chars
3. Strip control characters    — keeps \\t \\n \\r; removes rest
4. Length cap 4000             — blocked if exceeded (WhatsApp max is 4096)
5. Pattern match               — 10+ OWASP LLM01 patterns, case-insensitive

``SanitizeResult.blocked=True`` → caller sends T-05/T-06 template and does NOT
forward the raw text to the LLM. ``blocked=False`` → use ``.cleaned`` as the
normalized input to the LLM.

INVARIANT: This source file must NEVER contain invisible Unicode codepoints
(U+200B..U+200F, U+202A..U+202E, U+2060..U+2064, U+FEFF) as literal characters.
Only ``tests/fixtures/kb_adversarial/04_hidden_chars.md`` is allowed to hold
such chars. Codepoints are referenced here via ``chr(0x...)`` calls only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger("security.prompt_firewall")


def _invisible_charset() -> str:
    """Build the character class string for invisible Unicode codepoints.

    Using chr() calls avoids embedding literal invisible chars in source.
    This is the compliance-safe way per the repo invariant above.
    """
    codepoints = [
        # Zero-width chars
        0x200B,  # ZERO WIDTH SPACE
        0x200C,  # ZERO WIDTH NON-JOINER
        0x200D,  # ZERO WIDTH JOINER
        0x200E,  # LEFT-TO-RIGHT MARK
        0x200F,  # RIGHT-TO-LEFT MARK
        # Bidirectional controls (LRE/RLE/PDF/LRO/RLO)
        0x202A,  # LEFT-TO-RIGHT EMBEDDING
        0x202B,  # RIGHT-TO-LEFT EMBEDDING
        0x202C,  # POP DIRECTIONAL FORMATTING
        0x202D,  # LEFT-TO-RIGHT OVERRIDE
        0x202E,  # RIGHT-TO-LEFT OVERRIDE  (RTL attack)
        # Word joiner and invisible math operators
        0x2060,  # WORD JOINER
        0x2061,  # FUNCTION APPLICATION
        0x2062,  # INVISIBLE TIMES
        0x2063,  # INVISIBLE SEPARATOR
        0x2064,  # INVISIBLE PLUS
        0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
    ]
    return "".join(chr(cp) for cp in codepoints)


# Built once at module load — re.compile is O(1) per match after this.
INVISIBLE_CHARS_PATTERN: re.Pattern[str] = re.compile(f"[{_invisible_charset()}]")

# Control chars to strip: 0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f, 0x7f
# Preserves \t (0x09), \n (0x0a), \r (0x0d).
CONTROL_CHARS: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_LENGTH: int = 4000  # cap below WhatsApp's 4096 limit

# OWASP LLM01 prompt-injection pattern catalog (minimum 10 entries).
# All compiled with re.I (case-insensitive); applied post-NFKC-normalization.
INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(previous|above|all)\s+(instructions?|context)", re.I),
    re.compile(r"you\s+are\s+now\s+(a\s+)?", re.I),
    re.compile(r"(system|instruction|assistant|user)\s*:", re.I),
    re.compile(r"<\|.{0,20}\|>", re.I),  # sentinel tokens e.g. <|im_start|>
    re.compile(r"(forget|disregard)\s+(everything|all)", re.I),
    re.compile(r"new\s+(role|persona|task|instructions?)", re.I),
    re.compile(r"\bDAN\b", re.I),  # "Do Anything Now" jailbreak
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"prompt\s+(injection|hack)", re.I),
    re.compile(r"output\s+(all|every|the)\s+(customer|user|client)\s+(data|info)", re.I),
    re.compile(r"reveal\s+(your|the)\s+(system|hidden)\s+(prompt|instructions?)", re.I),
]

__all__ = ["INJECTION_PATTERNS", "SanitizeResult", "sanitize"]


@dataclass
class SanitizeResult:
    """Result of running the prompt firewall over a raw inbound string."""

    blocked: bool
    reason: str = field(default="")
    cleaned: str = field(default="")


def sanitize(text: str) -> SanitizeResult:
    """Run the 5-step prompt firewall over ``text``.

    Returns ``SanitizeResult(blocked=False, cleaned=<normalized text>)`` on
    pass, or ``SanitizeResult(blocked=True, reason=<why>)`` on rejection.
    """
    # Step 1: NFKC normalization (collapses Unicode confusables/variants)
    # Note: NFKC does NOT remove zero-width or bidi chars — that is Step 2.
    normalized = unicodedata.normalize("NFKC", text)

    # Step 2: Strip invisible codepoints (built via chr() — no literal chars)
    cleaned = INVISIBLE_CHARS_PATTERN.sub("", normalized)

    # Step 3: Strip control characters (preserves whitespace: \t \n \r)
    cleaned = CONTROL_CHARS.sub("", cleaned)

    # Step 4: Length cap
    if len(cleaned) > MAX_LENGTH:
        log.info("prompt_firewall.scan", blocked=True, reason="length_exceeded")
        return SanitizeResult(blocked=True, reason="length_exceeded")

    # Step 5: Injection pattern matching (post-normalization for accuracy)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(cleaned):
            reason = f"pattern_match:{pattern.pattern[:30]}"
            log.info("prompt_firewall.scan", blocked=True, reason=reason)
            return SanitizeResult(blocked=True, reason=reason)

    return SanitizeResult(blocked=False, cleaned=cleaned)
