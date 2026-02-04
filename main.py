# main.py - WEBAPI BETWEEN COURIER SERVICE AND KITCHEN

from fastapi import FastAPI, Header, HTTPException, Depends
from datetime import datetime
from typing import Optional, Dict
import os
import logging
from pydantic import BaseModel
import json
from delivery_fanout import fanout_delivery_status
from sheets_sync import sync_delivery_status_to_kitchen
from delivery_fsm import is_valid_transition, is_final
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from courier_adapter import create_courier_order
from kitchen_context import load_registry
from kitchen_stubs import read_kitchen_catalog


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

load_registry()

log = logging.getLogger("webapi")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

_sheets_service = None

import base64
import tempfile

def get_sheets_service_safe():
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")

    if b64:
        try:
            creds_json = base64.b64decode(b64).decode("utf-8")
            creds_dict = json.loads(creds_json)

            credentials = Credentials.from_service_account_info(
                creds_dict,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
        except Exception as e:
            log.exception("FAILED TO LOAD GOOGLE CREDS FROM B64")
            raise RuntimeError("Invalid GOOGLE_SERVICE_ACCOUNT_B64") from e
    else:
        # fallback для локальной разработки
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

_KITCHEN_ADDRESS_CACHE: Dict[int, str] = {}

def get_kitchen_address_from_sheets(kitchen_id: int) -> Optional[str]:
    if kitchen_id in _KITCHEN_ADDRESS_CACHE:
        return _KITCHEN_ADDRESS_CACHE[kitchen_id]

    try:
        sheets = get_sheets_service_safe()
        result = sheets.values().get(
            spreadsheetId=get_kitchen_spreadsheet_id(kitchen_id),
            range="kitchen!A1:C1",
        ).execute()
    except Exception as e:
        log.error("[KITCHEN_SHEET_READ_FAILED] %s", e)
        return None

    values = result.get("values", [])
    if not values or len(values[0]) < 2:
        return None

    address = values[0][1]
    _KITCHEN_ADDRESS_CACHE[kitchen_id] = address
    return address

KITCHENS_REGISTRY = {
    1: {
        "spreadsheet_id": "1dQFxRHsS2yFSV5rzB_q4q5WLv2GPaB2Gyawm2ZudPx4",
        "name": "Восток & Азия",
        "city": "dunpo",
        "active": True,
        "tg_chat_id": 2115245228,
    },
    2: {
        "spreadsheet_id": "1oAFB9Xihqbdph217AEfXlPNTjuZVAlBr7UU4JDOmygQ",
        "name": "Tokyo Roll",
        "city": "dunpo",
        "active": True,
        "tg_chat_id": 2115245228,
    },
    3: {
        "spreadsheet_id": "1IUPf2cExtl2IyikgglEGIDE6tTVVd8B5lpaMee-U6GE",
        "name": "Русский Дом",
        "city": "dunpo",
        "active": True,
        "tg_chat_id": 2115245228,
    },
    4: {
        "spreadsheet_id": "1xjK95TRI4s-Q_5UuqEnpY0nKonhtg1qdsppNdcx9jHQ",
        "name": "Urban Grill",
        "city": "dunpo",
        "active": True,
        "tg_chat_id": 2115245228,
    },
    5: {
        "spreadsheet_id": "1aLAOt31_sR6POGxqfq3ouAqoMt2dyBjw80908SZFF_Q",
        "name": "Street Food Hub",
        "city": "dunpo",
        "active": True,
        "tg_chat_id": 2115245228,
    },
}

# ===== Delivery price stub (MVP) =====
MIN_DELIVERY_PRICE_KRW = 4000
MAX_DELIVERY_DISTANCE_KM = 4.0  # стандартная зона доставки

# ===== Geocoding / zones config =====

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# TODO: zones enforcement later
# центры городов (пока один, расширяем потом)
CITY_ZONES = {
    "dunpo": {
        "center": (36.7694, 127.0806),  # Dunpo approx
        "radius_km": 4.0,
        "zone": "DUNPO",
    },
}

#===========1. App ===========#

from fastapi.middleware.cors import CORSMiddleware

# ===== lifespan =====

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    paths = []
    for r in app.router.routes:
        p = getattr(r, "path", None)
        m = getattr(r, "methods", None)
        if p:
            paths.append((p, ",".join(sorted(list(m))) if m else ""))
    log.info("[ROUTES] %s", paths)
    yield

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Kitchens (WebApp) =====

from typing import List

class KitchenOut(BaseModel):
    kitchen_id: int
    name: str
    city: Optional[str]
    status: str


@app.get("/api/v1/kitchens/{kitchen_id}/catalog")
def get_kitchen_catalog(kitchen_id: str):
    sheets = get_sheets_service_safe()
    return read_kitchen_catalog(
        sheets=sheets,
        kitchen_id=kitchen_id,
    )

from kitchen_context import list_kitchens, get

@app.get("/api/v1/kitchens")
def get_kitchens():
    kitchens = []

    for kitchen_id in list_kitchens():
        kitchen = get(kitchen_id)
        if not kitchen:
            continue

        kitchens.append({
            "kitchen_id": kitchen.kitchen_id,
            "name": kitchen.kitchen_id,  # пока так, без усложнений
            "city": kitchen.city,
            "status": kitchen.status,
        })

    return kitchens

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

class CourierStatusUpdate(BaseModel):
    status: str
    proof_image_file_id: Optional[str] = None
    proof_image_message_id: Optional[str] = None

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


#Заказ#

class OrderCreateRequest(BaseModel):
    order_id: str
    source: str
    kitchen_id: Optional[int] = None
    client_tg_id: int
    client_name: str
    client_phone: str
    pickup_address: str
    delivery_address: str
    pickup_eta_at: Optional[datetime] = None
    city: str
    comment: Optional[str] = None

    # 👇 ПРИНИМАЕМ ЦЕНУ ОТ WEBAPP
    delivery_price: Optional[int] = None


class OrderCreateResponse(BaseModel):
    status: str
    external_delivery_ref: Optional[str] = None
    already_exists: bool = False


#Статус от курьерки#
from typing import Optional, Dict, Any
class OrderStatusUpdate(BaseModel):
    status: str
    eta_minutes: Optional[int] = None
    proof_image_file_id: Optional[str] = None
    proof_image_message_id: Optional[int] = None


class PickupETARequest(BaseModel):
    pickup_eta_at: datetime
    source: str = "preset"


#5. Геокодинг и зоны (STUB)#

import requests
from fastapi.concurrency import run_in_threadpool

async def geocode_address(address: str) -> Optional[tuple[float, float]]:
    # ✅ ДОБАВИТЬ
    if not address or not address.strip():
        log.warning("[GEOCODE] Empty address, skipping")
        return None
    if not GOOGLE_MAPS_API_KEY:
        log.warning("GOOGLE GEOCODE SKIP: API KEY MISSING")
        return None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY,
    }

    try:
        r = await run_in_threadpool(requests.get, url, params=params, timeout=5)
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.exception("GOOGLE GEOCODE ERROR")
        return None

    if data.get("status") != "OK":
        return None

    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

# TODO: zones enforcement later
def check_zone(city: str, lat: float, lng: float) -> dict:
    city_cfg = CITY_ZONES.get(city.lower())
    if not city_cfg:
        return {
            "zone": None,
            "distance_km": None,
            "outside_zone": True,
        }

    clat, clng = city_cfg["center"]
    distance = haversine_km(lat, lng, clat, clng)

    return {
        "zone": city_cfg["zone"],
        "distance_km": round(distance, 2),
        "outside_zone": distance > city_cfg["radius_km"],
    }

import math

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_delivery_price(distance_km: float) -> int:
    base = MIN_DELIVERY_PRICE_KRW
    variable = distance_km * 900 * 1.6
    total = base + variable

    # округление до 100
    rounded = int(round(total / 100) * 100)
    return max(rounded, MIN_DELIVERY_PRICE_KRW)



#6. Address check (STUB) #

class AddressCheckRequest(BaseModel):
    address: str
    kitchen_id: int
    city: str | None = None

class AddressCheckResponse(BaseModel):
    ok: bool
    normalized_address: str
    zone: Optional[str] = None
    message: Optional[str] = None
    # 👇 КРИТИЧНО ДЛЯ VUE
    delivery_price: Optional[int] = None
    distance_km: Optional[float] = None

@app.post("/api/v1/validate-address", response_model=AddressCheckResponse, dependencies=[Depends(require_api_key)])
async def validate_address_alias(payload: AddressCheckRequest):
    return await _check_address_impl(payload)

@app.post("/api/v1/validate-address", response_model=AddressCheckResponse, dependencies=[Depends(require_api_key)])
async def check_address(payload: AddressCheckRequest):

    kitchen_id = payload.kitchen_id
    kitchen_address = get_kitchen_address_from_sheets(kitchen_id)

    if not kitchen_address:
        return AddressCheckResponse(
            ok=False,
            normalized_address=payload.address,
            zone=None,
            message="Адрес кухни не задан",
        )

    kitchen_coords = await geocode_address(kitchen_address)
    client_coords = await geocode_address(payload.address)

    if not kitchen_coords or not client_coords:
        return AddressCheckResponse(
            ok=False,
            normalized_address=payload.address,
            zone=None,
            message="Не удалось определить координаты",
        )

    distance_km = haversine_km(
        kitchen_coords[0], kitchen_coords[1],
        client_coords[0], client_coords[1],
    )

    distance_km = haversine_km(
        kitchen_coords[0], kitchen_coords[1],
        client_coords[0], client_coords[1],
    )

    price = calculate_delivery_price(distance_km)

    # зона теперь ИНФОРМАЦИОННАЯ
    STANDARD_ZONE_KM = 4.0

    outside_zone = distance_km > STANDARD_ZONE_KM

    return AddressCheckResponse(
        ok=True,
        normalized_address=payload.address,
        zone=payload.city,
        delivery_price=price,  # 👈 ВАЖНО
        distance_km=round(distance_km, 2),
        message=(
            f"Адрес вне стандартной зоны ({round(distance_km,1)} км). "
            f"Стоимость доставки {price} ₩"
            if outside_zone
            else f"Стоимость доставки {price} ₩"
        ),
    )
# ===== WebApp order models =====

from typing import List

class WebAppOrderItem(BaseModel):
    name: str
    qty: int

class WebAppPaymentProof(BaseModel):
    upload_id: str

class WebAppDelivery(BaseModel):
    address: str
    price_krw: int

class WebAppOrderCreateRequest(BaseModel):
    order_id: str

    # ✅ Telegram user id (из WebApp)
    user_id: Optional[int] = None

    kitchen_id: int
    city: str
    items: List[WebAppOrderItem]
    total_price: int
    delivery: WebAppDelivery
    comment: Optional[str] = None
    payment: WebAppPaymentProof


#=====================Endpoint создания заказа====================#

@app.post(
    "/api/v1/webapp/orders",
    dependencies=[Depends(require_api_key)],
)
async def create_webapp_order(payload: WebAppOrderCreateRequest):
    # 1) kitchen context
    kitchen_key = (
        payload.kitchen_id
        if isinstance(payload.kitchen_id, str)
        else f"kitchen_{payload.kitchen_id}"
    )

    kitchen = get(kitchen_key)
    if not kitchen:
        raise HTTPException(
            status_code=404,
            detail=f"Kitchen not found: {kitchen_key}",
        )

    # 2) idempotency: проверка по ORDERS
    if payload.order_id in ORDERS:
        return {
            "status": "ok",
            "order_id": payload.order_id,
            "already_exists": True,
        }

    # 3) проверка upload_id
    upload_id = payload.payment.upload_id

    # допускаем upload_id из WebApp без in-memory проверки
    # existence проверяется позже, при работе кухни
    if not upload_id.startswith("upload_"):
        raise HTTPException(
            status_code=400,
            detail="Invalid payment proof id",
        )

    # 4) items -> legacy string
    items_str = "; ".join(
        f"{i.name} x{i.qty}" for i in payload.items
    )

    # 5) synthetic user
    

    # 6) row_values (A:AD)
    row_values = [
        payload.order_id,                         # A order_id
        datetime.utcnow().isoformat(),            # B created_at

        payload.user_id or "",                    # C user_id
        "",                                       # D username

        items_str,                                # E items
        payload.total_price,                      # F total_price

        "Доставка",                               # G type
        payload.comment or "",                    # H comment

        f"upload:{upload_id}",                    # I payment_proof

        "created",                                # J status

        "",                                       # K handled_at
        "",                                       # L handled_by
        "",                                       # M reaction_seconds

        payload.delivery.address,                 # N address
        payload.delivery.price_krw,               # O delivery_fee

        "webapp",                                 # P source

        "",                                       # Q staff_message_id
        "",                                       # R pickup_eta_at
        "",                                       # S eta_source

        "delivery_new",                           # T delivery_state

        "",                                       # U courier_status_raw
        "",                                       # V courier_external_id
        "",                                       # W courier_external_id (legacy)
        "",                                       # X courier_status_detail
        "",                                       # Y courier_last_error
        "",                                       # Z courier_sent_at

        "",                                       # AA delivery_confirmed_at
        "",                                       # AB platform_commission
        "created",                                # AC commission_status
        "",                                       # AD owner_debt_snapshot
    ]

    # 7) append в Sheets
    sheets = get_sheets_service_safe()
    sheets.values().append(
        spreadsheetId=kitchen.spreadsheet_id,
        range="orders!A:AD",
        valueInputOption="RAW",
        body={"values": [row_values]},
    ).execute()

    # 8) минимальная регистрация в памяти
    ORDERS[payload.order_id] = {
        "order_id": payload.order_id,
        "source": "webapp",
        "kitchen_id": payload.kitchen_id,
        "status": "created",
        "delivery_state": "delivery_new",
    }

    log.info("[WEBAPP_ORDER_CREATED] %s", payload.order_id)

    return {
        "status": "ok",
        "order_id": payload.order_id,
    }

#7. Создание заказа (idempotent)#

@app.post(
    "/api/v1/orders",
    response_model=OrderCreateResponse,
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("kitchen")),
    ],
)
async def create_order(payload: OrderCreateRequest):

    log.info(
        "[CREATE_ORDER] order_id=%s source=%s kitchen_id=%s courier_requested=%s",
        payload.order_id,
        payload.source,
        payload.kitchen_id,
        payload.pickup_eta_at is not None,
    )

    print(">>> USING create_courier_order FROM", create_courier_order.__module__)

    kitchen_address = get_kitchen_address_from_sheets(payload.kitchen_id)
    kitchen_coords = await geocode_address(kitchen_address)
    client_coords = await geocode_address(payload.delivery_address)

    delivery_price = MIN_DELIVERY_PRICE_KRW
    price_source = "fallback"

    # ✅ 1) если WebApp уже посчитал цену — берем её
    if payload.delivery_price is not None and payload.delivery_price > 0:
        delivery_price = payload.delivery_price
        price_source = "from_webapp"

    # ✅ 2) иначе считаем сами (fallback)
    elif kitchen_coords and client_coords:
        distance_km = haversine_km(
            kitchen_coords[0], kitchen_coords[1],
            client_coords[0], client_coords[1],
        )
        delivery_price = calculate_delivery_price(distance_km)
        price_source = "google_distance"

    # STUB: default kitchen_id
    if payload.kitchen_id is None:
        payload.kitchen_id = 1

    # 1. idempotency
    if payload.order_id in ORDERS:
        return OrderCreateResponse(
            status="ok",
            external_delivery_ref=ORDERS[payload.order_id].get("delivery_order_id"),
            already_exists=True,
        )

    # 2. определяем решение кухни
    courier_requested = payload.pickup_eta_at is not None

    delivery_order_id = None
    delivery_provider = None

    log.info("[DEBUG] calling courier_adapter.create_courier_order")

    # 3. если курьер нужен - дергаем курьерку
    if courier_requested:
        courier_payload = {
            "order_id": payload.order_id,
            "source": payload.source,
            "kitchen_id": payload.kitchen_id or 1,
            "client_tg_id": payload.client_tg_id,
            "client_name": payload.client_name,
            "client_phone": payload.client_phone,
            "pickup_address": payload.pickup_address,
            "delivery_address": payload.delivery_address,
            "pickup_eta_at": payload.pickup_eta_at.isoformat() if payload.pickup_eta_at else None,
            "city": payload.city,
            "comment": payload.comment,
            "price_krw": delivery_price,
        }
        log.error("[DEBUG COURIER PAYLOAD] %s", courier_payload)
        try:
            delivery_order_id = await create_courier_order(courier_payload)
            delivery_provider = "courier"
        except Exception as e:
            log.error("[COURIER_CREATE_FAILED] %s", e)

            # ⬇️ ВАЖНО: заказ ВСЕ РАВНО создается
            delivery_order_id = None
            delivery_provider = None

            # фиксируем проблему в заказе
            courier_failed = True
        else:
            courier_failed = False

    # 4. сохраняем заказ в Web API
    # Важно: стартовый delivery-статус для FSM должен быть delivery_new, если курьер реально запрошен.
    ORDERS[payload.order_id] = {
        **payload.dict(),

        # решение кухни зафиксировано ВСЕГДА
        "courier_decision": (
            "requested" if courier_requested else "not_requested"
        ),

        # стартовый FSM-статус
        # ❗ даже если курьерка упала, FSM должен стартовать
        "status": (
            "delivery_new"
            if courier_requested
            else "courier_not_requested"
        ),

        # ===== delivery price (STUB) =====
        "delivery_price_krw": delivery_price,
        "delivery_price_source": price_source,

        # курьерка может быть временно недоступна
        # ❗ ВАЖНО: provider = courier, если доставка ЗАПРОШЕНА
        # иначе update_order_status и fanout будут игнорить
        "delivery_provider": (
            "courier" if courier_requested else None
        ),

        # внешний id может отсутствовать — это допустимо
        "delivery_order_id": delivery_order_id,

        # 🆕 фиксируем сбой курьерки, НЕ ломая заказ
        "courier_failed": (
            courier_requested and delivery_order_id is None
        ),

        # 🆕 для диагностики и будущих ретраев
        "courier_last_error": None if delivery_order_id else "courier_create_failed",
        "kitchen_tg_chat_id": KITCHENS_REGISTRY.get(payload.kitchen_id, {}).get("tg_chat_id", 0),
        "created_at": datetime.utcnow().isoformat(),
    }

    emit_event(
        "order_created",
        payload.order_id,
        {
            "courier_requested": courier_requested,
            "courier_failed": ORDERS[payload.order_id]["courier_failed"],
        },
    )

    # 5. ответ
    return OrderCreateResponse(
        status="ok",
        external_delivery_ref=delivery_order_id,
        already_exists=False,
    )


@app.post(
    "/api/v1/orders/{order_id}/pickup_eta",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("kitchen")),
    ],
)
async def set_pickup_eta(order_id: str, payload: PickupETARequest):
    print(">>> USING create_courier_order FROM", create_courier_order.__module__)

    order = ORDERS.get(order_id)
    if not order:
        log.warning(
            "[STATUS_404] order_id=%s not found in ORDERS. Known=%s",
            order_id,
            list(ORDERS.keys()),
        )
        raise HTTPException(status_code=404, detail="Order not found")

    # защита от повторов
    if order.get("pickup_eta_at"):
        return {"status": "ok", "already_set": True}

    # фиксируем ETA
    order["pickup_eta_at"] = payload.pickup_eta_at.isoformat()
    order["pickup_eta_source"] = payload.source

    # решение кухни
    order["courier_decision"] = "requested"
    order["status"] = "delivery_new"

    # формируем payload для курьерки
    courier_payload = {
            "order_id": payload.order_id,
            "source": payload.source,
            "kitchen_id": payload.kitchen_id or 1,
            "client_tg_id": payload.client_tg_id,
            "client_name": payload.client_name,
            "client_phone": payload.client_phone,
            "pickup_address": payload.pickup_address,
            "delivery_address": payload.delivery_address,
            "pickup_eta_at": payload.pickup_eta_at.isoformat() if payload.pickup_eta_at else None,
            "city": payload.city,
            "comment": payload.comment,
            "price_krw": delivery_price,
        }
    log.error("[DEBUG COURIER PAYLOAD] %s", courier_payload)
    try:
        print(">>> USING create_courier_order FROM", create_courier_order.__module__)
        delivery_order_id = await create_courier_order(courier_payload)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Courier service unavailable",
        )

    order["delivery_provider"] = "courier"
    order["delivery_order_id"] = delivery_order_id

    return {
        "status": "ok",
        "delivery_order_id": delivery_order_id,
    }


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
        order = next(
            (
                o for o in ORDERS.values()
                if o.get("delivery_order_id") == order_id
            ),
            None,
        )

    if not order:
        try:
            restored = load_order_from_sheets(order_id)
            if restored:
                ORDERS[restored["order_id"]] = restored
                order = restored
                log.warning(
                    "[ORDER_RESTORED_FROM_SHEETS] order_id=%s",
                    restored["order_id"],
                )
        except Exception as e:
            log.error(
                "[ORDER_RESTORE_FAILED] order_id=%s err=%s",
                order_id,
                e,
            )

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

    # 1️⃣ сначала получаем заказ
    order = ORDERS.get(order_id)

    # 1.1) fallback: ищем по external delivery_order_id
    if not order:
        order = next(
            (
                o for o in ORDERS.values()
                if o.get("delivery_order_id") == order_id
            ),
            None,
        )

    # 1.2) EXCLUSIVE: восстановление из Sheets
    # (оставляем как есть ниже)

    # ✅ eta можно писать только когда order уже найден
    if order and payload.eta_minutes is not None:
        order["eta_minutes"] = payload.eta_minutes
    log.info(
        "[COURIER_STATUS] incoming order_id=%s status=%s",
        order_id,
        payload.status,
    )
    # 1.2) EXCLUSIVE: восстановление из Sheets
    if not order:
        try:
            restored = load_order_from_sheets(order_id)
            if restored:
                canonical_id = restored.get("order_id")
                ORDERS[canonical_id] = restored
                order = restored
                log.warning(
                    "[ORDER_RESTORED_FROM_SHEETS] external_id=%s canonical_id=%s",
                    order_id,
                    canonical_id,
                )
        except Exception as e:
            log.error(
                "[ORDER_RESTORE_FAILED] order_id=%s err=%s",
                order_id,
                e,
            )

    if not order:
        log.error(
            "[STATUS_404] order_id=%s not found in ORDERS. Known=%s",
            order_id,
            list(ORDERS.keys()),
        )
        raise HTTPException(status_code=404, detail="Order not found")

    # canonical id, если запрос пришел по external delivery id
    canonical_id = order.get("order_id") or order_id

    # 2️⃣ защита: курьер не вызывался
    if order.get("courier_decision") == "not_requested":
        return {
            "status": "ignored",
            "reason": "courier_not_requested",
        }

    # ❗ только kitchen-orders участвуют в fan-out и уведомлениях
    if order.get("source") != "kitchen":
        log.info(
            "Ignore courier status update: not a kitchen order | order_id=%s",
            order_id,
        )
        return {
            "status": "ignored",
            "reason": "not_kitchen_order",
        }

    # защита от апдейтов не от курьерки
    # ⬇️ НО: разрешаем fan-out если курьер упал при создании (DEV / STUB)
    if order.get("delivery_provider") != "courier":
        if not order.get("courier_failed"):
            return {
                "status": "ignored",
                "reason": "not_managed_by_courier",
            }
        log.warning(
            "[DEV_STUB] delivery_provider missing but courier_failed=True | order_id=%s",
            order_id,
        )

    print(
        "[DEBUG] updating order",
        order_id,
        "kitchen_id",
        order.get("kitchen_id"),
    )

    courier_status = payload.status

    # гарантируем инициализацию
    mapped_status = None

    # optional proof from courier
    if payload.proof_image_file_id:
        order["proof_image_file_id"] = payload.proof_image_file_id
    if payload.proof_image_message_id:
        order["proof_image_message_id"] = payload.proof_image_message_id

    raw_current_status = order.get("status")
    current_status = raw_current_status

    # === важно: фиксируем первый courier-апдейт ===
    first_courier_update = "courier_updated_at" not in order

    # pending - технический статус Web API, FSM его не видит
    if current_status == "pending":
        current_status = None

    # 2. всегда сохраняем raw courier-статус
    order["courier_status_detail"] = courier_status
    order["courier_updated_at"] = datetime.utcnow().isoformat()

    # 3. маппинг статуса
    mapped_status = map_courier_status_to_kitchen(courier_status)
    from delivery_fsm import is_valid_transition, is_final

    # 1) неизвестный статус курьерки
    if not mapped_status:
        order["courier_last_error"] = f"Unknown courier status: {courier_status}"
        emit_event(
            "delivery_status_unknown",
            canonical_id,
            {"courier_status": courier_status},
        )
        return {"status": "ok"}

    # 2) idempotent (НО не для первого courier-апдейта)
    if mapped_status == current_status and not first_courier_update:
        return {"status": "ok", "idempotent": True}

    # 3) финальные состояния immutable (кроме idempotent, он уже выше)
    if is_final(current_status):
        emit_event(
            "delivery_status_ignored_final",
            canonical_id,
            {
                "current": current_status,
                "incoming": mapped_status,
                "courier_status": courier_status,
            },
        )
        return {"status": "ok", "final": True}

    # 4) FSM-проверка допустимости перехода
    if not is_valid_transition(current_status, mapped_status):
        order["courier_last_error"] = (
            f"Invalid transition {current_status} -> {mapped_status}"
        )

        emit_event(
            "delivery_status_rejected",
            canonical_id,
            {
                "current": current_status,
                "incoming": mapped_status,
                "courier_status": courier_status,
            },
        )

        # === FINAL SYNC TO SHEETS ===
        sync_delivery_status_to_kitchen(
            sheets=get_sheets_service_safe(),
            spreadsheet_id=get_kitchen_spreadsheet_id(order["kitchen_id"]),
            canonical_id=canonical_id,
            delivery_state=mapped_status,
            courier_status_raw=courier_status,
            courier_external_id=order.get("delivery_order_id"),
            courier_status_detail=order.get("courier_status_detail"),
            courier_last_error=order.get("courier_last_error"),
        )
        return {"status": "ok", "rejected": True}
    
    # ===== HAPPY PATH =====

    # 5) применяем новый статус
    order["status"] = mapped_status

    if payload.proof_image_file_id:
        order["proof_image_file_id"] = payload.proof_image_file_id

    if payload.proof_image_message_id:
        order["proof_image_message_id"] = payload.proof_image_message_id

    try:
        fanout_delivery_status(
            order=order,
            courier_status=courier_status,
            kitchen_status=mapped_status,
        )
    except Exception as e:
        order["fanout_last_error"] = str(e)

    delivery_external_id = order.get("delivery_order_id")

    # первый courier-апдейт ВСЕГДА синкаем
    if current_status is None or delivery_external_id:
        sync_delivery_status_to_kitchen(
            sheets=get_sheets_service_safe(),
            spreadsheet_id=get_kitchen_spreadsheet_id(order["kitchen_id"]),
            canonical_id=canonical_id,
            delivery_state=mapped_status,
            courier_status_raw=courier_status,
            courier_external_id=delivery_external_id,  # может быть None - это ОК
            courier_status_detail=order.get("courier_status_detail"),
            courier_last_error=order.get("courier_last_error"),
            delivery_confirmed_at=(
                datetime.utcnow().isoformat()
                if mapped_status == "delivered"
                else None
            ),
        )

    emit_event(
        "delivery_status_changed",
        canonical_id,
        {
            "from": raw_current_status,
            "to": mapped_status,
            "courier_status": courier_status,
        },
    )

    # 7. delivered - финал (один раз)
    if mapped_status == "delivered" and not order.get("delivery_confirmed_at"):
        order["delivery_confirmed_at"] = datetime.utcnow().isoformat()

        emit_event(
            "delivery_completed",
            canonical_id,
            {
                "proof_image_file_id": order.get("proof_image_file_id"),
                "proof_image_message_id": order.get("proof_image_message_id"),
            },
        )

    return {"status": "ok"}

# ===== WebApp uploads (payment proof) =====

from fastapi import UploadFile, File
from uuid import uuid4
from fastapi.responses import FileResponse
import os

UPLOADS_DIR = os.getenv("UPLOADS_DIR", "./uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

UPLOADS = {}  # upload_id -> meta


@app.post(
    "/api/v1/uploads/payment-proof",
    dependencies=[Depends(require_api_key)],
)
async def upload_payment_proof(file: UploadFile = File(...)):
    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(status_code=415, detail="Unsupported file type")

    upload_id = f"upload_{uuid4().hex}"

    ext = ".jpg" if file.content_type == "image/jpeg" else ".png"
    path = os.path.join(UPLOADS_DIR, upload_id + ext)

    with open(path, "wb") as f:
        f.write(await file.read())

    UPLOADS[upload_id] = {
        "path": path,
        "content_type": file.content_type,
        "created_at": datetime.utcnow().isoformat(),
    }

    log.info("[UPLOAD] payment proof saved upload_id=%s", upload_id)

    return {
        "status": "ok",
        "upload_id": upload_id,
    }


@app.get("/api/v1/uploads/payment-proof/{upload_id}")
def get_payment_proof(upload_id: str):
    meta = UPLOADS.get(upload_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Upload not found")

    return FileResponse(
        path=meta["path"],
        media_type=meta["content_type"],
    )
# ===== WebAPP =====

from typing import Union

def parse_kitchen_id(value: Union[str, int]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.startswith("kitchen_"):
        tail = s.split("kitchen_", 1)[1]
        if tail.isdigit():
            return int(tail)
        return None
    if s.isdigit():
        return int(s)
    return None


class AddressCheckRequest(BaseModel):
    address: str
    kitchen_id: Union[str, int]
    city: str | None = None


class AddressCheckResponse(BaseModel):
    ok: bool
    normalized_address: str
    zone: Optional[str] = None
    message: Optional[str] = None
    delivery_price: Optional[int] = None
    distance_km: Optional[float] = None


async def _check_address_impl(payload: AddressCheckRequest) -> AddressCheckResponse:
    kitchen_id_int = parse_kitchen_id(payload.kitchen_id)
    if not kitchen_id_int:
        return AddressCheckResponse(
            ok=False,
            normalized_address=payload.address,
            zone=None,
            message="kitchen_id не найден",
        )

    kitchen_address = get_kitchen_address_from_sheets(kitchen_id_int)
    if not kitchen_address:
        return AddressCheckResponse(
            ok=False,
            normalized_address=payload.address,
            zone=None,
            message="Адрес кухни не задан",
        )

    kitchen_coords = await geocode_address(kitchen_address)
    client_coords = await geocode_address(payload.address)

    if not kitchen_coords or not client_coords:
        return AddressCheckResponse(
            ok=False,
            normalized_address=payload.address,
            zone=None,
            message="Не удалось определить координаты",
        )

    distance_km = haversine_km(
        kitchen_coords[0], kitchen_coords[1],
        client_coords[0], client_coords[1],
    )

    price = calculate_delivery_price(distance_km)

    STANDARD_ZONE_KM = 4.0
    outside_zone = distance_km > STANDARD_ZONE_KM

    return AddressCheckResponse(
        ok=True,
        normalized_address=payload.address,
        zone=payload.city,
        delivery_price=price,
        distance_km=round(distance_km, 2),
        message=(
            f"Адрес вне стандартной зоны ({round(distance_km,1)} км). Стоимость доставки {price} ₩"
            if outside_zone
            else f"Стоимость доставки {price} ₩"
        ),
    )


@app.post(
    "/api/v1/address/check",
    response_model=AddressCheckResponse,
    dependencies=[Depends(require_api_key)],
)
async def check_address(payload: AddressCheckRequest):
    return await _check_address_impl(payload)


# алиас под фронт, чтобы не менять Vue еще 10 раз
@app.post(
    "/api/v1/address/check",
    response_model=AddressCheckResponse,
    dependencies=[Depends(require_api_key)],
)
async def validate_address(payload: AddressCheckRequest):
    return await _check_address_impl(payload)

# ===== Utilities =====

def load_order_from_sheets(order_id: str) -> dict | None:
    """
    Восстановление заказа из Google Sheets.
    Используется ТОЛЬКО если заказа нет в памяти.

    Поддерживает поиск:
    - по canonical order_id (колонка C)
    - по external delivery_order_id (колонка W)

    Работает с несколькими кухнями.
    """
    try:
        sheets = get_sheets_service_safe()

        for kitchen_id, kitchen in KITCHENS_REGISTRY.items():
            spreadsheet_id = kitchen["spreadsheet_id"]

            result = sheets.values().get(
                spreadsheetId=spreadsheet_id,
                range="orders!A:AD",
            ).execute()

            rows = result.get("values", [])

            # индексы (0-based)
            IDX_ORDER_ID = 2           # C
            IDX_STATUS = 19            # T
            IDX_DELIVERY_ORDER_ID = 22 # W

            for row in rows:
                canon = row[IDX_ORDER_ID] if len(row) > IDX_ORDER_ID else ""
                ext = row[IDX_DELIVERY_ORDER_ID] if len(row) > IDX_DELIVERY_ORDER_ID else ""

                if canon == order_id or (ext and ext == order_id):
                    return {
                        "order_id": canon or order_id,
                        "kitchen_id": kitchen_id,
                        "client_tg_id": (
                            int(row[1])
                            if len(row) > 1 and str(row[1]).isdigit()
                            else None
                        ),
                        "status": row[IDX_STATUS] if len(row) > IDX_STATUS else None,
                        "delivery_order_id": ext or None,
                        "courier_decision": "requested",
                        "delivery_provider": "courier",
                        "source": "kitchen",
                        "city": kitchen.get("city"),
                        "pickup_address": None,
                        "delivery_address": None,
                    }

    except Exception as e:
        log.error(f"[SHEETS_RESTORE_FAILED] {order_id} {e}")

    return None


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

# ===== Endpoint приема статуса =====

@app.post(
    "/api/v1/courier/status",
    dependencies=[
        Depends(require_api_key),
        Depends(require_role("courier")),
    ],
)
def courier_status_webhook(payload: CourierStatusWebhook):
    """
    Единая точка приема статусов от курьерки.
    Делегирует обработку в update_order_status.
    """

    log.info(
        "[COURIER_WEBHOOK] order_id=%s status=%s proof=%s",
        payload.order_id,
        payload.status,
        bool(payload.proof_image_file_id),
    )

    # 1) пытаемся найти заказ по внутреннему order_id
    order = ORDERS.get(payload.order_id)

    # 2) fallback: ищем по external delivery_order_id
    if not order:
        order = next(
            (
                o for o in ORDERS.values()
                if o.get("delivery_order_id") == payload.order_id
            ),
            None,
        )

    # 3) ленивое восстановление из Sheets
    if not order:
        try:
            restored = load_order_from_sheets(payload.order_id)
            if restored:
                ORDERS[restored["order_id"]] = restored
                order = restored
                log.warning(
                    "[ORDER_RESTORED_FROM_SHEETS] order_id=%s",
                    restored["order_id"],
                )
        except Exception as e:
            log.error(
                "[ORDER_RESTORE_FAILED] order_id=%s err=%s",
                payload.order_id,
                e,
            )

    if not order:
        log.error(
            "[STATUS_404] order_id=%s not found in ORDERS. Known=%s",
            payload.order_id,
            list(ORDERS.keys()),
        )
        raise HTTPException(status_code=404, detail="Order not found")

    # ✅ ГЛАВНОЕ ИСПРАВЛЕНИЕ: вызываем обновление статуса
    canonical_id = order.get("order_id") or payload.order_id
    
    try:
        # Создаем объект OrderStatusUpdate из webhook payload
        status_update = OrderStatusUpdate(
            status=payload.status,
            proof_image_file_id=payload.proof_image_file_id,
            proof_image_message_id=payload.proof_image_message_id,
            eta_minutes=payload.eta_minutes,
        )
        
        # Вызываем основную функцию обновления статуса
        result = update_order_status(canonical_id, status_update)
        
        log.info(
            "[COURIER_WEBHOOK] processed | order_id=%s result=%s",
            payload.order_id,
            result,
        )
        
        return result
        
    except Exception as e:
        log.exception(
            "[COURIER_WEBHOOK] failed to process | order_id=%s",
            payload.order_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process webhook: {str(e)}"
        )

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
    except Exception as e:
        log.error("[EMIT_EVENT_FAILED] %s", e)



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





print("### WEB API MAIN LOADED ###")

