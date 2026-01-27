from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict
import uuid

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
            "order_id": order_id,
            "payload": payload or {},
        }
        # В MVP просто логируем; позже подключим запись в sheets/events
        print(f"[EVENT] {event}")
    except Exception:
        # fan-out никогда не должен ломать основной флоу
        pass

#Заказ#

class OrderCreateRequest(BaseModel):
    order_id: str
    source: str
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
            delivery_order_id=ORDERS[payload.order_id].get("delivery_order_id"),
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
        delivery_order_id=delivery_order_id,
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

ALLOWED_TRANSITIONS = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"completed"},
}

@app.post(
    "/api/v1/orders/{order_id}/status",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("courier")),
    ],
)
def update_order_status(order_id: str, payload: OrderStatusUpdate):
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    current = order["status"]
    new = payload.status

    if new not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail="Invalid status transition")

    mapped_status = map_courier_status_to_kitchen(new)

    if not mapped_status:
        order["courier_last_error"] = f"Unknown courier status: {new}"
        return {"status": "ignored"}

    order["courier_status_detail"] = new
    
    order["updated_at"] = datetime.utcnow().isoformat()
    
    order["status"] = mapped_status

    emit_event(
        "delivery_status_changed",
        payload.order_id,
        {
            "courier_status": courier_status,
            "kitchen_status": mapped_status,
        },
    )

    if mapped_status == "delivered":
        order["delivery_confirmed_at"] = datetime.utcnow().isoformat()
        emit_event(
            "delivery_completed",
            payload.order_id,
            {
                "proof_image_file_id": order.get("proof_image_file_id"),
                "proof_image_message_id": order.get("proof_image_message_id"),
            },
        )

    # fan-out stub
    # emit_event("order_status_changed", ...)

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
    mapped_status = map_courier_status_to_kitchen(courier_status)

    # сохраняем raw статус всегда
    order["courier_status_detail"] = courier_status
    order["courier_updated_at"] = datetime.utcnow().isoformat()

    if not mapped_status:
        order["courier_last_error"] = f"Unknown courier status: {courier_status}"
        return {"status": "ignored"}

    # защита от регрессий
    if order.get("status") == mapped_status:
        return {"status": "ok", "idempotent": True}

    order["status"] = mapped_status

    if mapped_status == "delivered":
        order["delivery_confirmed_at"] = datetime.utcnow().isoformat()

        if payload.meta:
            if "proof_image_file_id" in payload.meta:
                order["proof_image_file_id"] = payload.meta["proof_image_file_id"]
            if "proof_image_message_id" in payload.meta:
                order["proof_image_message_id"] = payload.meta["proof_image_message_id"]

    return {"status": "ok"}

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