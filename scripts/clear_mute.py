"""One-off: limpiar el mute de un número en Redis (QA/soporte).

Uso (con el entorno del servicio, para que REDIS_URL resuelva):
    railway run -s landa-agent-service python scripts/clear_mute.py +573123528153

Idempotente: borra la clave bot:muted:<e164> si existe.
"""

from __future__ import annotations

import asyncio
import os
import sys

import redis.asyncio as aioredis


async def main() -> None:
    if len(sys.argv) < 2:
        print("uso: python scripts/clear_mute.py <phone_e164>")
        sys.exit(2)
    phone = sys.argv[1].strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    url = os.environ.get("REDIS_URL")
    if not url:
        print("REDIS_URL no está en el entorno")
        sys.exit(1)
    r = aioredis.from_url(url)
    key = f"bot:muted:{phone}"
    existed = await r.delete(key)
    print(f"deleted={existed} key={key}")
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
