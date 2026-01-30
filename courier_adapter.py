import httpx
import os

COURIER_API_URL = "http://127.0.0.1:9000"
API_KEY = os.getenv("API_KEY", "DEV_KEY")

async def create_courier_order(payload: dict) -> str:
    print("USING create_courier_order FROM courier_adapter")

    timeout = httpx.Timeout(5.0, connect=3.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        courier_payload = {
            "order_id": payload["order_id"],
            "source": payload["source"],
            "client_tg_id": payload["client_tg_id"],
            "client_name": payload["client_name"],
            "client_phone": payload["client_phone"],
            "pickup_address": payload["pickup_address"],
            "delivery_address": payload["delivery_address"],
            "pickup_eta_at": payload["pickup_eta_at"],
            "city": payload["city"],
            "comment": payload.get("comment"),
        }

        resp = await client.post(
            f"{COURIER_API_URL}/api/v1/orders",
            json=courier_payload,
            headers={
                "X-API-KEY": API_KEY,
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
