# main.py - WEBAPI BETWEEN COURIER SERVICE AND KITCHEN

from fastapi import FastAPI, Header, HTTPException, Depends
from datetime import datetime
from typing import Optional, Dict
import uuid
import json
import os
import logging
from pydantic import BaseModel

from delivery_fanout import fanout_delivery_status
from sheets_sync import sync_delivery_status_to_kitchen
from delivery_fsm import is_valid_transition, is_final
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from courier_adapter import create_courier_order

log = logging.getLogger("webapi")
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

# ===== Delivery price stub (MVP) =====
MIN_DELIVERY_PRICE_KRW = 4000
DELIVERY_PRICE_SOURCE = "stub_min_tariff"

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


#Заказ#

class OrderCreateRequest(BaseModel):
    order_id: str
    source: str
    kitchen_id: Optional[int] = None  # STUB: allow default kitchen
    client_tg_id: int
    client_name: str
    client_phone: str
    pickup_address: str
    delivery_address: str
    pickup_eta_at: Optional[datetime] = None
    city: str
    comment: Optional[str] = None


class OrderCreateResponse(BaseModel):
    status: str
    external_delivery_ref: Optional[str] = None
    already_exists: bool = False


#Статус от курьерки#

class OrderStatusUpdate(BaseModel):
    status: str


class PickupETARequest(BaseModel):
    pickup_eta_at: datetime
    source: str = "preset"


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


#6. Address check (STUB) #

class AddressCheckRequest(BaseModel):
    city: str
    address: str


class AddressCheckResponse(BaseModel):
    ok: bool
    normalized_address: str
    zone: Optional[str] = None
    message: Optional[str] = None


@app.post(
    "/api/v1/address/check",
    response_model=AddressCheckResponse,
    dependencies=[Depends(require_api_key)],
)
def check_address(payload: AddressCheckRequest):
    """
    STUB v0:
    - любой адрес считается корректным
    - всегда inside zone
    - ничего не блокируем
    """

    normalized = (payload.address or "").strip()

    return AddressCheckResponse(
        ok=True,
        normalized_address=normalized,
        zone="STUB_ZONE",
        message="Адрес принят (stub)",
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
async def create_order(payload: OrderCreateRequest):

    log.info(
        "[CREATE_ORDER] order_id=%s source=%s kitchen_id=%s courier_requested=%s",
        payload.order_id,
        payload.source,
        payload.kitchen_id,
        payload.pickup_eta_at is not None,
    )

    print(">>> USING create_courier_order FROM", create_courier_order.__module__)

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
            "client_tg_id": payload.client_tg_id,
            "client_name": payload.client_name,
            "client_phone": payload.client_phone,
            "pickup_address": payload.pickup_address,
            "delivery_address": payload.delivery_address,
            "pickup_eta_at": payload.pickup_eta_at.isoformat(),
            "city": payload.city,
            "comment": payload.comment,
        }

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
        "delivery_price_krw": MIN_DELIVERY_PRICE_KRW,
        "delivery_price_source": DELIVERY_PRICE_SOURCE,

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
        "order_id": order["order_id"],
        "source": order["source"],
        "client_tg_id": order["client_tg_id"],
        "client_name": order["client_name"],
        "client_phone": order["client_phone"],
        "pickup_address": order["pickup_address"],
        "delivery_address": order["delivery_address"],
        "pickup_eta_at": order["pickup_eta_at"],
        "city": order["city"],
        "comment": order.get("comment"),
    }

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
    order["updated_at"] = datetime.utcnow().isoformat()

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

# ===== Utilities =====

def load_order_from_sheets(order_id: str) -> dict | None:
    """
    Восстановление заказа из Google Sheets.
    Используется ТОЛЬКО если заказа нет в памяти.

    EXCLUSIVE:
    - поддерживает поиск как по canonical order_id (колонка C),
      так и по external delivery_order_id (колонка W по твоему листу, но проверим индексы ниже).
    """
    try:
        sheets = get_sheets_service_safe()

        result = sheets.values().get(
            spreadsheetId=get_kitchen_spreadsheet_id(1),
            range="orders!A2:Z",
        ).execute()

        rows = result.get("values", [])

        # индексы (0-based) внутри A..Z
        IDX_ORDER_ID = 2        # C
        IDX_STATUS = 19         # T? ты используешь 19 выше, оставляем
        IDX_DELIVERY_ORDER_ID = 22  # W? у тебя было 22, оставляем как было

        for row in rows:
            # safe getters
            canon = row[IDX_ORDER_ID] if len(row) > IDX_ORDER_ID else ""
            ext = row[IDX_DELIVERY_ORDER_ID] if len(row) > IDX_DELIVERY_ORDER_ID else ""

            # совпадение по canonical или по external
            if canon == order_id or (ext and ext == order_id):
                return {
                    "order_id": canon or order_id,
                    "kitchen_id": 1,
                    "client_tg_id": int(row[1]) if len(row) > 1 and str(row[1]).isdigit() else None,
                    "status": row[IDX_STATUS] if len(row) > IDX_STATUS else None,
                    "delivery_order_id": ext or None,
                    "courier_decision": "requested",
                    "delivery_provider": "courier",
                    "source": "kitchen",
                    "city": None,
                    "pickup_address": None,
                    "delivery_address": None,
                    # важно: чтобы update_order_status не игнорировал
                }

    except Exception as e:
        log.error(f"[SHEETS_RESTORE_FAILED] {order_id} {e}")

    return None

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
    Не содержит UI-логики.
    Не отправляет Telegram напрямую.

    Делегирует всю бизнес-логику в /api/v1/orders/{order_id}/status,
    чтобы не было расхождения между двумя обработчиками.
    """

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

    # 3) 🆕 ленивое восстановление из Sheets (эксклюзивное исключение)
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
