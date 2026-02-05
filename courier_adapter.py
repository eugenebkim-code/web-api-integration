# courier_adapter.py
# Version: 2.0 with detailed logging

import httpx
import os
import traceback

COURIER_API_URL = os.getenv("COURIER_API_URL")

print(f"🔥🔥🔥 COURIER_ADAPTER MODULE LOADED 🔥🔥🔥")
print(f"🔥 COURIER_API_URL = {COURIER_API_URL!r}")

if not COURIER_API_URL:
    raise RuntimeError("COURIER_API_URL is not set")

API_KEY = os.getenv("COURIER_API_KEY", "DEV_KEY")
print(f"🔥 API_KEY = {API_KEY[:3]}...{API_KEY[-3:] if len(API_KEY) > 6 else API_KEY}")


def _get_kitchen_address(kitchen_id: int) -> str:
    """
    Fallback адрес кухни.
    TODO: Загружать из конфига или БД.
    """
    KITCHEN_ADDRESSES = {
        1: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
        2: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
        3: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
        4: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
        5: "충남 아산시 둔포면 둔포중앙로161번길 21-2",
    }
    return KITCHEN_ADDRESSES.get(kitchen_id, "충남 아산시 둔포면 둔포중앙로161번길 21-2")


async def create_courier_order(payload: dict) -> str:
    import sys
    print(f"🔥🔥🔥 FUNCTION CALLED FROM: {__file__}")
    print(f"🔥🔥🔥 FUNCTION: {sys._getframe().f_code.co_name}")
    print("=" * 80)
    print("🚀 NEW VERSION courier_adapter.create_courier_order CALLED")
    """
    Отправляет заказ в курьерскую службу.
    
    Args:
        payload: dict с полями order_id, kitchen_id, pickup_address, etc.
        
    Returns:
        delivery_order_id от курьерки
    """
    print("=" * 80)
    print("🚀 NEW VERSION courier_adapter.create_courier_order CALLED")
    print("=" * 80)
    
    try:
        # 1. Получаем kitchen_id
        kitchen_id = payload.get("kitchen_id", 1)
        print(f"📍 Step 1: kitchen_id = {kitchen_id}")
        
        # 2. Определяем pickup_address
        # ВАЖНО: Не затираем адрес если он уже есть в payload!
        pickup_address = payload.get("pickup_address")
        if not pickup_address or pickup_address == "":
            print(f"⚠️  pickup_address пустой в payload, используем fallback")
            pickup_address = _get_kitchen_address(kitchen_id)
        else:
            print(f"✅ pickup_address уже есть в payload")
        
        print(f"📍 Step 2: pickup_address = {pickup_address!r}")
        
        # 3. Формируем courier_payload
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
        
        print(f"📍 Step 3: courier_payload сформирован")
        print(f"   order_id: {courier_payload['order_id']}")
        print(f"   pickup_address: {courier_payload['pickup_address']!r}")
        print(f"   delivery_address: {courier_payload['delivery_address']!r}")
        print(f"   pickup_eta_at: {courier_payload['pickup_eta_at']}")
        print(f"   city: {courier_payload['city']}")
        print(f"   price_krw: {courier_payload['price_krw']}")
        
        # 4. Готовим HTTP запрос
        url = f"{COURIER_API_URL}/api/v1/orders"
        print(f"📍 Step 4: URL = {url}")
        print(f"   API_KEY = {API_KEY[:3]}...{API_KEY[-3:]}")
        
        timeout = httpx.Timeout(5.0, connect=3.0)
        
        # 5. Отправляем запрос
        print(f"📍 Step 5: Отправляем POST запрос...")
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=courier_payload,
                headers={
                    "X-API-KEY": API_KEY,
                },
            )
        
        print(f"📍 Step 6: Получен ответ")
        print(f"   Status: {resp.status_code}")
        print(f"   Body: {resp.text[:500]}")
        
        # 6. Проверяем статус
        if resp.status_code != 200:
            print(f"❌ ERROR: Курьерка вернула {resp.status_code}")
            raise RuntimeError(
                f"Courier error {resp.status_code}: {resp.text}"
            )
        
        # 7. Парсим ответ
        data = resp.json()
        print(f"📍 Step 7: Response JSON = {data}")
        
        delivery_order_id = data.get("delivery_order_id")
        if not delivery_order_id:
            print(f"❌ ERROR: В ответе нет delivery_order_id!")
            raise RuntimeError("Courier response missing delivery_order_id")
        
        print(f"✅ SUCCESS: delivery_order_id = {delivery_order_id}")
        print("=" * 80)
        
        return delivery_order_id
        
    except Exception as e:
        print(f"❌ EXCEPTION в courier_adapter.create_courier_order:")
        print(f"   Type: {type(e).__name__}")
        print(f"   Message: {str(e)}")
        print(f"   Traceback:")
        traceback.print_exc()
        print("=" * 80)
        raise
