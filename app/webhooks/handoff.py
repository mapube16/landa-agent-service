"""Lambda-proyect handoff endpoint — Plan 04-07 (D-19, D-20, D-21, D-22, D-23).

``POST /case/handoff/no_answer`` is the trigger the voice agent calls when a
call went unanswered: it opens a payment Case and sends the Meta UTILITY
template that re-opens the WhatsApp channel with the deudor.

TDD RED stub — implementation lands in the GREEN commit.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/case", tags=["handoff"])


class NoAnswerHandoff(BaseModel):
    """Body contract for the lambda-proyect no-answer handoff (D-19)."""

    phone: str = Field(pattern=r"^\+\d{8,15}$")  # E.164 (T-04-07-04)
    cliente_nombre: str = Field(min_length=1, max_length=80)
    numero_poliza: str = Field(min_length=1, max_length=40)
    case_id: uuid.UUID


def _verify_bearer(authorization: str | None = Header(None)) -> None:
    raise NotImplementedError


@router.post("/handoff/no_answer", dependencies=[Depends(_verify_bearer)])
async def handoff_no_answer(body: NoAnswerHandoff, request: Request) -> dict[str, str | bool]:
    raise NotImplementedError
