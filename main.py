from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict
import uuid
import json
from delivery_fanout import fanout_delivery_status
from sheets_sync import sync_delivery_status_to_kitchen
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

_sheets_service = None

def get_sheets_service_safe():
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    _sheets_service = build(
        "sheets", "v4", credentials=credentials
    ).spreadsheets()

    return _sheets_service

def get_kitchen_spreadsheet_id(kitchen_id: int) -> str:
    kitchen = KITCHENS_REGISTRY.get(kitchen_id)
    if not kitchen:
        raise RuntimeError(f"kitchen {kitchen_id} not found")
    return kitchen["spreadsheet_id"]

KITCHENS_REGISTRY = {
    1: {
        "spreadsheet_id": "1dQFxRHsS2yFSV5rzB_q4q5WLv2GPaB2Gyawm2ZudPx4",
        "city": "dunpo",
        "active": True,
    }
}

#===========1. App ===========#

app = FastAPI(
    title="Unified Web API",
    version="1.0",
)

#2. Простая auth / роли (заглушка)#

API_KEY = "DEV_KEY"

def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def require_role(required: str):
    def _check(x_role: str = Header(...)):
        if x_role != required:
            raise HTTPException(status_code=403, detail="Forbidden")
    return _check

#3. In-memory storage (потом заменим)#

ORDERS: Dict[str, dict] = {}
ADDRESSES: Dict[int, dict] = {}
EVENTS_SHEET = "events"

EVENTS_HEADERS = [
    "ts",          # A
    "event",       # B
    "order_id",    # C
    "payload_json" # D
]


#4. Models#

class CourierStatusWebhook(BaseModel):
    order_id: str
    status: str
    meta: Optional[dict] = None

class AddressVerifyRequest(BaseModel):
    tg_id: int
    address: str

class AddressVerifyResponse(BaseModel):
    status: str
    verified: bool
    zone: Optional[str]
    distance_km: Optional[float]
    outside_zone: bool
    message: str

# ===== Events (fan-out base) =====

def emit_event(event_type: str, order_id: str, payload: dict | None = None):
    try:
        event = {
            "ts": datetime.utcnow().isoformat(),
            "event": event_type,
            "order_id": str(order_id),
            "payload": payload or {},
        }
        print("[EVENT]", event)
    except Exception:
        pass

#Заказ#

class OrderCreateRequest(BaseModel):
    order_id: str
    source: str
    kitchen_id: int
    client_tg_id: int
    client_name: str
    client_phone: str
    pickup_address: str
    delivery_address: str
    pickup_eta_at: datetime
    city: str
    comment: Optional[str] = None

class OrderCreateResponse(BaseModel):
    status: str
    external_delivery_ref: Optional[str] = None
    already_exists: bool = False

#Статус от курьерки#

class OrderStatusUpdate(BaseModel):
    status: str


#5. Геокодинг и зоны (STUB)#
def geocode_address(address: str):
    # stub
    return 37.0, 127.0

def check_zone(lat: float, lng: float):
    # stub
    return {
        "zone": "DUNPO",
        "distance_km": 3.2,
        "outside_zone": False,
    }
#6. Адрес: verify#

@app.post(
    "/api/v1/address/verify",
    response_model=AddressVerifyResponse,
    dependencies=[Depends(require_api_key)],
)
def verify_address(payload: AddressVerifyRequest):
    lat, lng = geocode_address(payload.address)
    zone_info = check_zone(lat, lng)

    ADDRESSES[payload.tg_id] = {
        "address": payload.address,
        "lat": lat,
        "lng": lng,
        "verified": True,
        "verified_at": datetime.utcnow().isoformat(),
        **zone_info,
    }

    return AddressVerifyResponse(
        status="ok",
        verified=True,
        zone=zone_info["zone"],
        distance_km=zone_info["distance_km"],
        outside_zone=zone_info["outside_zone"],
        message="Адрес проверен",
    )

#7. Создание заказа (idempotent)#

@app.post(
    "/api/v1/orders",
    response_model=OrderCreateResponse,
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("kitchen")),
    ],
)
def create_order(payload: OrderCreateRequest):
    # idempotency
    if payload.order_id in ORDERS:
        return OrderCreateResponse(
            status="ok",
            external_delivery_ref=ORDERS[payload.order_id].get("delivery_order_id"),
            already_exists=True,
        )

    delivery_order_id = f"courier-{uuid.uuid4()}"

    ORDERS[payload.order_id] = {
        **payload.dict(),
        "status": "pending",

        # delivery (external)
        "delivery_provider": "external",
        "delivery_status": "external",
        "delivery_order_id": delivery_order_id,

        "created_at": datetime.utcnow().isoformat(),
    }

    return OrderCreateResponse(
        status="ok",
        external_delivery_ref=delivery_order_id,
        already_exists=False,
    )

#8. Получение заказа (курьерка)#

@app.get(
    "/api/v1/orders/{order_id}",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("courier")),
    ],
)
def get_order(order_id: str):
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


#9. Обновление статуса (курьерка)#


@app.post(
    "/api/v1/orders/{order_id}/status",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("courier")),
    ],
)
def update_order_status(order_id: str, payload: OrderStatusUpdate):

    # 1. получаем заказ
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    print(
        "[DEBUG] updating order",
        order_id,
        "kitchen_id",
        order.get("kitchen_id"),
    )

    courier_status = payload.status
    current_status = order.get("status")

    # 2. всегда сохраняем raw courier-статус
    order["courier_status_detail"] = courier_status
    order["courier_updated_at"] = datetime.utcnow().isoformat()

    # 3. маппинг статуса
    mapped_status = map_courier_status_to_kitchen(courier_status)

    if not mapped_status:
        order["courier_last_error"] = f"Unknown courier status: {courier_status}"

        emit_event(
            "delivery_status_unknown",
            order_id,
            {"courier_status": courier_status},
        )
        return {"status": "ok"}

    # 4. защита от регрессий
    if mapped_status == current_status:
        return {"status": "ok", "idempotent": True}

    # 5. sync в Sheets кухни (ТОЛЬКО если курьер реально вызывался)
    if order.get("delivery_order_id"):
        
        sync_delivery_status_to_kitchen(
            sheets=get_sheets_service_safe(),
            spreadsheet_id=get_kitchen_spreadsheet_id(order["kitchen_id"]),
            order_id=order_id,
            delivery_state=mapped_status,
            courier_status_raw=courier_status,
            courier_external_id=order.get("delivery_order_id"),
            courier_status_detail=order.get("courier_status_detail"),
            courier_last_error=order.get("courier_last_error"),
        )

    # 6. fan-out уведомлений
    fanout_delivery_status(
        order=order,
        courier_status=courier_status,
        kitchen_status=mapped_status,
    )

    # 7. применяем новый статус
    order["status"] = mapped_status
    order["updated_at"] = datetime.utcnow().isoformat()

    emit_event(
        "delivery_status_changed",
        order_id,
        {
            "courier_status": courier_status,
            "kitchen_status": mapped_status,
        },
    )

    # 8. delivered — финал
    if mapped_status == "delivered":
        order["delivery_confirmed_at"] = datetime.utcnow().isoformat()

        emit_event(
            "delivery_completed",
            order_id,
            {
                "proof_image_file_id": order.get("proof_image_file_id"),
                "proof_image_message_id": order.get("proof_image_message_id"),
            },
        )

    return {"status": "ok"}

# ===== Endpoint приема статуса ===== 

@app.post(
    "/api/v1/courier/status",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("courier")),
    ],
)

def courier_status_webhook(payload: CourierStatusWebhook):
    order = ORDERS.get(payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    courier_status = payload.status

    # всегда сохраняем raw статус
    order["courier_status_detail"] = courier_status
    order["courier_updated_at"] = datetime.utcnow().isoformat()

    mapped_status = map_courier_status_to_kitchen(courier_status)

    if not mapped_status:
        order["courier_last_error"] = f"Unknown courier status: {courier_status}"
        return {"status": "ignored"}

    # защита от повторов
    if order.get("status") == mapped_status:
        return {"status": "ok", "idempotent": True}

    # уведомления клиенту (fail-safe)
    if mapped_status == "delivery_in_progress":
        notify_client_safe(order, "🚚 Курьер выехал")

    if courier_status == "order_on_hands":
        notify_client_safe(order, "📦 Заказ у курьера")

    if mapped_status == "delivered":
        notify_client_safe(order, "✅ Заказ доставлен")

    # применяем статус в памяти
    order["status"] = mapped_status

    # sync в Sheets ТОЛЬКО если доставка реально существует
    if order.get("delivery_order_id"):
        sync_delivery_status_to_kitchen(
            sheets=get_sheets_service_safe(),
            spreadsheet_id=get_kitchen_spreadsheet_id(order["kitchen_id"]),
            order_id=order["order_id"],
            courier_status_raw=courier_status,
            courier_external_id=order.get("delivery_order_id"),
            courier_status_detail=order.get("courier_status_detail"),
            is_delivered=(mapped_status == "delivered"),
        )

    # fan-out (не ломает поток)
    fanout_delivery_status(
        order=order,
        courier_status=courier_status,
        kitchen_status=mapped_status,
    )

    # delivered — финал (один раз)
    if mapped_status == "delivered" and not order.get("delivery_confirmed_at"):
        order["delivery_confirmed_at"] = datetime.utcnow().isoformat()

        if payload.meta:
            if "proof_image_file_id" in payload.meta:
                order["proof_image_file_id"] = payload.meta["proof_image_file_id"]
            if "proof_image_message_id" in payload.meta:
                order["proof_image_message_id"] = payload.meta["proof_image_message_id"]

    return {"status": "ok"}

def notify_client_safe(order: dict, text: str):
    """
    Fail-safe уведомление клиента.
    Ошибки не пробрасываются и не ломают основной флоу.
    """
    try:
        # STUB: здесь позже будет реальный вызов бота курьерки
        print(f"[notify_client] tg={order['client_tg_id']} | {text}")
    except Exception as e:
        # ничего не ломаем, максимум фиксируем
        order["last_client_notify_error"] = str(e)

# ===== Courier -> Kitchen status mapping =====

COURIER_TO_KITCHEN_STATUS = {
    "created": "delivery_new",
    "courier_assigned": "delivery_in_progress",
    "courier_departed": "delivery_in_progress",
    "order_on_hands": "delivery_in_progress",
    "delivered": "delivered",
    "cancelled": "cancelled",
}

def map_courier_status_to_kitchen(courier_status: str) -> str | None:
    return COURIER_TO_KITCHEN_STATUS.get(courier_status)

#10. Заказы клиента (WebApp / курьерка)#

@app.get(
    "/api/v1/clients/{client_tg_id}/orders",
    dependencies=[Depends(require_api_key)],
)
def get_client_orders(client_tg_id: int):
    return [
        o for o in ORDERS.values()
        if o["client_tg_id"] == client_tg_id
    ]

print("### WEB API MAIN LOADED ###")