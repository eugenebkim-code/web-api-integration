#courier_adapter.py

import httpx
import os

COURIER_API_URL = os.getenv("COURIER_API_URL")

if not COURIER_API_URL:
    raise RuntimeError("COURIER_API_URL is not set")

API_KEY = os.getenv("COURIER_API_KEY", "DEV_KEY")

def _get_kitchen_address(kitchen_id: int) -> str:
    """
    Fallback адрес кухни по ID.
    Используется только если pickup_address не передан в payload.
    """
    KITCHEN_ADDRESSES = {
        1: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
        # Добавьте другие кухни по мере необходимости:
        # 2: "адрес второй кухни",
        # 3: "адрес третьей кухни",
    }
    return KITCHEN_ADDRESSES.get(kitchen_id, "충남 아산시 둔포면 둔포중앙로161번길 21-2")

async def create_courier_order(payload: dict) -> str:
    print(">>> USING create_courier_order FROM courier_adapter")

    kitchen_id = payload.get("kitchen_id", 1)
    
    # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ:
    # НЕ перетираем pickup_address если он уже передан в payload
    pickup_address = payload.get("pickup_address")
    if not pickup_address:
        pickup_address = _get_kitchen_address(kitchen_id)
        print(f"[COURIER_ADAPTER] pickup_address was empty, using fallback for kitchen {kitchen_id}")
    
    print(f"[COURIER_ADAPTER] kitchen_id={kitchen_id} pickup_address={pickup_address!r}")

    timeout = httpx.Timeout(5.0, connect=3.0)

    courier_payload = {
        "order_id": payload["order_id"],
        "source": payload["source"],
        "client_tg_id": payload["client_tg_id"],
        "client_name": payload["client_name"],
        "client_phone": payload["client_phone"],
        "pickup_address": pickup_address,  # ✅ используем проверенный адрес
        "delivery_address": payload["delivery_address"],
        "pickup_eta_at": payload["pickup_eta_at"],
        "city": payload["city"],
        "comment": payload.get("comment"),
        "price_krw": payload.get("price_krw", 0),
    }
    
    # ✅ DEBUG: Подробное логирование отправляемых данных
    print(f"[COURIER_ADAPTER] Sending to {COURIER_API_URL}/api/v1/orders:")
    print(f"  order_id: {courier_payload['order_id']}")
    print(f"  pickup_address: {courier_payload['pickup_address']!r}")
    print(f"  delivery_address: {courier_payload['delivery_address']!r}")
    print(f"  pickup_eta_at: {courier_payload['pickup_eta_at']}")
    print(f"  city: {courier_payload['city']}")
    print(f"🔥 [COURIER_ADAPTER] About to POST to: {COURIER_API_URL}/api/v1/orders")
    print(f"🔥 [COURIER_ADAPTER] Payload: {courier_payload}")
    print(f"🔥 [COURIER_ADAPTER] Headers: X-API-KEY={API_KEY}")
    async with httpx.AsyncClient(timeout=timeout) as client:
        
        resp = await client.post(
            f"{COURIER_API_URL}/api/v1/orders",
            json=courier_payload,
            headers={
                "X-API-KEY": API_KEY,
            },
        )

    print(f"🔥 [COURIER_ADAPTER] Response status: {resp.status_code}")
    print(f"🔥 [COURIER_ADAPTER] Response body: {resp.text}")   

    if resp.status_code != 200:
        print(f"[COURIER_ADAPTER] ❌ ERROR Response:")
        print(f"  Status: {resp.status_code}")
        print(f"  Body: {resp.text}")
        raise RuntimeError(
            f"Courier error {resp.status_code}: {resp.text}"
        )

    data = resp.json()

    delivery_order_id = data.get("delivery_order_id")
    if not delivery_order_id:
        print(f"[COURIER_ADAPTER] ❌ ERROR: Missing delivery_order_id in response: {data}")
        raise RuntimeError("Courier response missing delivery_order_id")

    print(f"[COURIER_ADAPTER] ✅ SUCCESS: delivery_order_id={delivery_order_id}")
    return delivery_order_id
