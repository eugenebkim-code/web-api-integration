import httpx
import os

COURIER_API_URL = os.getenv("COURIER_API_URL", "http://127.0.0.1:9000")
COURIER_API_KEY = os.getenv("COURIER_API_KEY", "DEV_KEY")

async def create_courier_order(payload: dict) -> str:
    print("USING create_courier_order FROM courier_adapter")

    timeout = httpx.Timeout(5.0, connect=3.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{COURIER_API_URL}/api/v1/orders",
            json=payload,
            headers={
                "X-API-KEY": COURIER_API_KEY,
                "X-ROLE": "integration",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Courier error {resp.status_code}: {resp.text}"
        )

    data = resp.json()

    delivery_order_id = data.get("delivery_order_id")
    if not delivery_order_id:
        raise RuntimeError("Courier response missing delivery_order_id")

    return delivery_order_id
