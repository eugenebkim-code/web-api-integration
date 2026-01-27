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

    order["status"] = new
    order["updated_at"] = datetime.utcnow().isoformat()

    # fan-out stub
    # emit_event("order_status_changed", ...)

    return {"status": "ok"}

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