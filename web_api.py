# web_api.py

import os
import uuid
import json
import base64
import logging
import re
from datetime import datetime
from typing import List, Optional
from notifications import notify_staff_from_web

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from telegram import Bot
from telegram.constants import ParseMode

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# -------------------------------------------------
# BASE DIR / FILES
# -------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

log = logging.getLogger("WEB_API")

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
# GOOGLE SHEETS
# -------------------------------------------------

service_account_info = json.loads(
    base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
)

credentials = Credentials.from_service_account_info(
    service_account_info,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)

sheets_service = build("sheets", "v4", credentials=credentials)

def append_row(range_name: str, row: list):
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

# -------------------------------------------------
# TELEGRAM
# -------------------------------------------------

bot = Bot(token=BOT_TOKEN)

# -------------------------------------------------
# MODELS
# -------------------------------------------------

class OrderItem(BaseModel):
    id: str
    name: str
    qty: int
    price: int


class OrderCustomer(BaseModel):
    user_id: int
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

    screenshotBase64: Optional[str] = None
    createdAt: str

# -------------------------------------------------
# APP
# -------------------------------------------------

app = FastAPI(title="BARAKAT Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def save_screenshot(order_id: str, data: str) -> str:
    if data.startswith("data:"):
        m = re.match(r"^data:image/[^;]+;base64,(.+)$", data)
        if not m:
            raise ValueError("Invalid screenshot base64")
        data = m.group(1)

    raw = base64.b64decode(data)
    filename = f"order_{order_id}.jpg"
    path = os.path.join(SCREENSHOTS_DIR, filename)

    with open(path, "wb") as f:
        f.write(raw)

    return f"screenshots/{filename}"

# -------------------------------------------------
# ENDPOINT
# -------------------------------------------------

@app.post("/order")
async def create_order(order: OrderIn, request: Request):
    log.info("=== /order ===")
    log.info(f"IP: {request.client.host}")

    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    c = order.customer

    # USERS
    append_row("users!A:F", [
        c.user_id,
        c.username or "",
        c.full_name or "",
        created_at,
        c.name,
        c.phone,
    ])

    # ITEMS
    items_str = "; ".join(
        f"{item.name} x{item.qty}" for item in order.items
    )

    # SCREENSHOT
    screenshot_path = ""
    if order.screenshotBase64:
        screenshot_path = save_screenshot(order_id, order.screenshotBase64)

    # ORDERS
    append_row("orders!A:Q", [
        order_id,
        created_at,
        c.user_id,
        c.username or "",
        items_str,
        order.pricing.grandTotal,
        c.deliveryType,
        c.comment or "",
        screenshot_path,
        "pending",
        "", "", "",
        c.address or "",
        order.pricing.delivery,
        "webapp",
        "",
    ])

    # --- TELEGRAM: notify via bot logic (with buttons) ---
    try:
        await notify_staff_from_web(
            bot=bot,
            order_id=order_id,
            order=order.model_dump(),
        )
    except Exception:
        log.exception("notify_staff_from_web failed")

    return {"ok": True, "order_id": order_id}
