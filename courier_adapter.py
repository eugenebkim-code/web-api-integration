import os
import httpx
import logging

log = logging.getLogger("COURIER_ADAPTER")

COURIER_API_URL = os.getenv("COURIER_API_URL")
COURIER_API_KEY = os.getenv("COURIER_API_KEY")
TIMEOUT = 10


async def create_courier_order(payload: dict) -> str:
    if not COURIER_API_URL:
        raise RuntimeError("COURIER_API_URL not set")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(
            f"{COURIER_API_URL}/api/v1/orders",
            json=payload,
            headers={"X-API-KEY": COURIER_API_KEY},
        )

    if r.status_code != 200:
        log.error("Courier create failed %s %s", r.status_code, r.text)
        raise RuntimeError("courier_create_failed")

    data = r.json()
    return data["delivery_order_id"]
