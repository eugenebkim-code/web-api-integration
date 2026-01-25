import os
import base64
import logging

from telegram import Bot

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


# -------------------------------------------------
# Main notify function
# -------------------------------------------------

async def notify_staff_from_web(order: dict):
    """
    order — полностью сформированный заказ из web_api
    """
    bot = Bot(token=BOT_TOKEN)

    customer = order["customer"]
    pricing = order["pricing"]
    items = order["items"]

    text_lines = [
        "🛒 *Новый заказ*",
        "",
        f"Имя: {customer.get('name')}",
        f"Телефон: {customer.get('phone')}",
        f"Тип: {'Доставка' if customer.get('deliveryType') == 'delivery' else 'Самовывоз'}",
    ]

    if customer.get("address"):
        text_lines.append(f"Адрес: {customer.get('address')}")

    if customer.get("comment"):
        text_lines.append(f"Комментарий: {customer.get('comment')}")

    text_lines.append("")
    text_lines.append("📦 *Товары:*")

    for item in items:
        text_lines.append(
            f"- {item['name']} × {item['qty']} = {item['price'] * item['qty']} ₩"
        )

    text_lines.append("")
    text_lines.append(f"Товары: {pricing['itemsTotal']} ₩")
    text_lines.append(f"Доставка: {pricing['delivery']} ₩")
    text_lines.append(f"*Итого: {pricing['grandTotal']} ₩*")

    message_text = "\n".join(text_lines)

    photo_bytes = None
    if order.get("screenshotBase64"):
        photo_bytes = decode_base64_image(order["screenshotBase64"])

    for chat_id in STAFF_CHAT_IDS:
        if photo_bytes:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_bytes,
                caption=message_text,
                parse_mode="Markdown",
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown",
            )

    log.info("notify_staff_from_web finished")