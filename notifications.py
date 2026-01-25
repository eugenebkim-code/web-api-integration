print("### NOTIFICATIONS MODULE LOADED (WITH BOT ARG)")
import os
import base64
import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

log = logging.getLogger("NOTIFY")

# -------------------------------------------------
# ENV
# -------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")

STAFF_CHAT_IDS = {
    int(x)
    for x in (os.getenv("STAFF_CHAT_IDS") or "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

# -------------------------------------------------
# Helpers
# -------------------------------------------------

def decode_base64_image(data: str) -> bytes:
    if "," in data:
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def build_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Принять",
                    callback_data=f"staff:approve:{order_id}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"staff:reject:{order_id}",
                ),
            ]
        ]
    )

# -------------------------------------------------
# Main notify function
# -------------------------------------------------

bot = Bot(token=BOT_TOKEN)

async def notify_staff_from_web(
    *,
    order_id: str,
    order: dict,
) -> Optional[object]:
    print("### notify_staff_from_web signature: bot, order_id, order")
    """
    Возвращает telegram.Message или None
    """

    customer = order.get("customer", {})
    pricing = order.get("pricing", {})
    items = order.get("items", [])

    text_lines = [
        "🛒 *Новый заказ*",
        "",
        f"Имя: {customer.get('name', '-')}",
        f"Телефон: {customer.get('phone', '-')}",
        f"Тип: {'Доставка' if customer.get('deliveryType') == 'delivery' else 'Самовывоз'}",
    ]

    if customer.get("address"):
        text_lines.append(f"Адрес: {customer['address']}")

    if customer.get("comment"):
        text_lines.append(f"Комментарий: {customer['comment']}")

    text_lines.append("")
    text_lines.append("📦 *Товары:*")

    for item in items:
        text_lines.append(
            f"- {item.get('name')} × {item.get('qty')} = {item.get('price', 0) * item.get('qty', 0)} ₩"
        )

    text_lines.extend(
        [
            "",
            f"Товары: {pricing.get('itemsTotal', 0)} ₩",
            f"Доставка: {pricing.get('delivery', 0)} ₩",
            f"*Итого: {pricing.get('grandTotal', 0)} ₩*",
        ]
    )

    message_text = "\n".join(text_lines)
    keyboard = build_keyboard(order_id)

    photo_bytes = None
    if order.get("screenshotBase64"):
        try:
            photo_bytes = decode_base64_image(order["screenshotBase64"])
        except Exception:
            log.warning("⚠️ screenshotBase64 decode failed", exc_info=True)

    sent_msg = None
    

    for chat_id in STAFF_CHAT_IDS:
        try:
            if photo_bytes:
                try:
                    sent_msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_bytes,
                        caption=message_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard,
                    )
                except BadRequest as e:
                    log.warning(
                        f"⚠️ sendPhoto failed, fallback to sendMessage for chat_id={chat_id}",
                        exc_info=True,
                    )
                    sent_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=keyboard,
                    )
            else:
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard,
                )

        except Exception:
            log.warning(
                f"⚠️ notify_staff failed for chat_id={chat_id}",
                exc_info=True,
            )
            continue

    return sent_msg