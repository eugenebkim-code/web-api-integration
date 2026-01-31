#courier_adapter.py

#courier_adapter.py

import httpx
import os

COURIER_API_URL = "http://127.0.0.1:9000"
API_KEY = os.getenv("API_KEY", "DEV_KEY")

# Ленивый импорт чтобы избежать circular import
def _get_kitchen_address(kitchen_id: int) -> str:
    from main import get_kitchen_address_from_sheets
    return get_kitchen_address_from_sheets(kitchen_id) or ""

async def create_courier_order(payload: dict) -> str:
    print("USING create_courier_order FROM courier_adapter")

    kitchen_id = payload.get("kitchen_id", 1)
    pickup_address = _get_kitchen_address(kitchen_id)
    
    print(f"[COURIER_ADAPTER] kitchen_id={kitchen_id} pickup_address={pickup_address}")

    timeout = httpx.Timeout(5.0, connect=3.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        courier_payload = {
            "order_id": payload["order_id"],
            "source": payload["source"],
            "client_tg_id": payload["client_tg_id"],
            "client_name": payload["client_name"],
            "client_phone": payload["client_phone"],
            "pickup_address": pickup_address,
            "delivery_address": payload["delivery_address"],
            "pickup_eta_at": payload["pickup_eta_at"],
            "city": payload["city"],
            "comment": payload.get("comment"),
            "price_krw": payload.get("price_krw", 0),
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
