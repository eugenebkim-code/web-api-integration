# web_api.py

import os
import uuid
import json
import base64
import logging
log = logging.getLogger("WEB_API")
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from telegram import Bot

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from notifications import notify_staff_from_web

GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")

if not GOOGLE_SERVICE_ACCOUNT_JSON_B64:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is missing")

service_account_info = json.loads(
    base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
)

credentials = Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)

sheets_service = build("sheets", "v4", credentials=credentials)

# -------------------------------------------------
# ENV
# -------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")

STAFF_CHAT_IDS = {
    int(x)
    for x in (os.getenv("STAFF_CHAT_IDS") or "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is missing")

if not GOOGLE_SERVICE_ACCOUNT_JSON_B64:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON_B64 is missing")


# -------------------------------------------------
# Google Sheets (Railway-safe)
# -------------------------------------------------

service_account_info = json.loads(
    base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
)

_sheets_credentials = Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)

_sheets_service = build("sheets", "v4", credentials=_sheets_credentials)


def append_row(range_name: str, row: list):
    sheets = _sheets_service.spreadsheets()
    sheets.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

# -------------------------------------------------
# Notify
# -------------------------------------------------
bot = Bot(token=BOT_TOKEN)


async def notify_staff_simple(order_id: str, text: str):
    for chat_id in STAFF_CHAT_IDS:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"notify failed for {chat_id}: {e}")

# -------------------------------------------------
# Models
# -------------------------------------------------
class OrderItem(BaseModel):
    id: str
    name: str
    qty: int
    price: int


class OrderCustomer(BaseModel):
    user_id: int                 # telegram user id
    username: Optional[str] = ""
    full_name: Optional[str] = ""

    name: str
    phone: str
    deliveryType: str
    address: Optional[str] = None
    comment: Optional[str] = None


class OrderPricing(BaseModel):
    itemsTotal: int
    delivery: int
    grandTotal: int


class OrderIn(BaseModel):
    customer: OrderCustomer
    items: List[OrderItem]
    pricing: OrderPricing

    screenshotName: Optional[str] = None
    screenshotBase64: Optional[str] = None  # ✅ data:image/...;base64,XXXX  или просто XXXX

    createdAt: str

# -------------------------------------------------
# App
# -------------------------------------------------

app = FastAPI(title="BARAKAT Web API")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev, позже сузим
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}

# -------------------------------------------------
# Helpers
# -------------------------------------------------

import base64
import re

def save_screenshot_file(order_id: str, screenshot_b64: str) -> str:
    """
    Возвращает относительный путь вида: screenshots/order_<order_id>.jpg
    screenshot_b64 может быть:
      - "data:image/jpeg;base64,AAA..."
      - "AAA..."
    """
    # вырезаем префикс data-url если есть
    if screenshot_b64.startswith("data:"):
        m = re.match(r"^data:image/([a-zA-Z0-9+.-]+);base64,(.+)$", screenshot_b64)
        if not m:
            raise ValueError("Invalid data-url base64")
        ext = m.group(1).lower()
        b64 = m.group(2)
    else:
        ext = "jpg"
        b64 = screenshot_b64

    # нормализуем расширение
    if ext in ("jpeg", "jpg"):
        ext = "jpg"
    elif ext == "png":
        ext = "png"
    else:
        ext = "jpg"

    filename = f"order_{order_id}.{ext}"
    abs_path = os.path.join(SCREENSHOTS_DIR, filename)

    raw = base64.b64decode(b64)
    with open(abs_path, "wb") as f:
        f.write(raw)

    # важно: возвращаем относительный путь, который поймет бот
    return f"screenshots/{filename}"

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

from fastapi import Request

@app.post("/order")
async def create_order(order: OrderIn, request: Request):
    log.info("=== /order called ===")
    log.info(f"Client IP: {request.client.host}")
    log.info(f"Customer user_id={order.customer.user_id}")
    log.info(f"Items count={len(order.items)}")
    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    c = order.customer

    # --- USERS ---
    append_row("users!A:F", [
        c.user_id,
        c.username or "",
        c.full_name or "",
        created_at,
        c.name,
        c.phone,
    ])

    # --- ITEMS ---
    items_str = "; ".join(
        f"{item.name} x{item.qty}" for item in order.items
    )

    # --- ORDERS ---
    payment_proof_value = ""

    # DEV MODE: если скрин не пришел, кладем фейковый путь
    if order.screenshotBase64:
        payment_proof_value = save_screenshot_file(order_id, order.screenshotBase64)
        log.info(f"Screenshot saved: {payment_proof_value}")
    else:
        payment_proof_value = "screenshots/dev_placeholder.jpg"

    append_row("orders!A:O", [
        order_id,
        created_at,
        c.user_id,                  # buyer_chat_id = telegram id
        c.username or "",
        items_str,
        order.pricing.grandTotal,
        c.deliveryType,
        c.comment or "",
        payment_proof_value,
        "pending",
        "", "", "",
        c.address or "",
        order.pricing.delivery,
    ])

    # --- TELEGRAM ---
    text = (
        "🛎 <b>Новый заказ (WebApp)</b>\n\n"
        f"🧾 ID: <code>{order_id}</code>\n"
        f"👤 Имя: {c.name}\n"
        f"📞 Телефон: {c.phone}\n"
        f"💰 Сумма: {order.pricing.grandTotal} ₩"
    )
    log.info(f"Calling notify_staff_from_web for order {order_id}")
    await notify_staff_from_web(order.model_dump())
    log.info("notify_staff_from_web finished")
    return {"ok": True, "order_id": order_id}
