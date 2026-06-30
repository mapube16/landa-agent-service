"""Payment graph module — node-name constants for Phase 4 (Wave 1 skeleton).

Wave 3 (Plan 04-04) populates the payment subgraph; this skeleton only declares
the node-name constants so other plans can import without runtime risk.

Node names match the Literal values added to ``QAState.node`` in 04-01 and the
``NODE_*`` constants used by the conditional routing edges in Plan 04-04.
"""

from __future__ import annotations

NODE_RECEIVE_COMPROBANTE: str = "node_receive_comprobante"
NODE_FORWARD_TO_CARTERA: str = "node_forward_to_cartera"
NODE_AWAITING_CARTERA: str = "node_awaiting_cartera"
NODE_CONFIRMING: str = "node_confirming"
NODE_PAYMENT_ESCALATE: str = "node_payment_escalate"

__all__ = [
    "NODE_AWAITING_CARTERA",
    "NODE_CONFIRMING",
    "NODE_FORWARD_TO_CARTERA",
    "NODE_PAYMENT_ESCALATE",
    "NODE_RECEIVE_COMPROBANTE",
]
