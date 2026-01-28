# delivery_fanout.py

import os
import logging
from telegram import Bot
from notifications import send_telegram_message

log = logging.getLogger("delivery_fanout")

# кухонные чаты (пока из env / конфига)
STAFF_CHAT_IDS = [
    int(x)
    for x in (os.getenv("STAFF_CHAT_IDS", "")).split(",")
    if x.strip().isdigit()
]


def fanout_delivery_status(order: dict, courier_status: str, kitchen_status: str):
    """
    Fan-out уведомлений.
    НИКОГДА не ломает основной поток.
    """
    try:
        notify_kitchen(order, courier_status, kitchen_status)
    except Exception:
        log.exception("notify_kitchen failed")

    try:
        notify_client(order, courier_status, kitchen_status)
    except Exception:
        log.exception("notify_client failed")


def notify_kitchen(order: dict, courier_status: str, kitchen_status: str):
    text = (
        "🚚 Обновление доставки\n\n"
        f"🧾 Заказ: {order.get('order_id')}\n"
        f"📦 Статус курьера: {courier_status}\n"
        f"🍽 Статус кухни: {kitchen_status}"
    )

    for chat_id in STAFF_CHAT_IDS:
        send_telegram_message(chat_id, text)


def notify_client(order: dict, courier_status: str, kitchen_status: str):
    client_tg_id = order.get("client_tg_id")
    if not client_tg_id:
        return

    STATUS_TEXT = {
        "courier_departed": "🚚 Курьер выехал",
        "order_on_hands": "📦 Заказ у курьера",
        "delivered": "✅ Заказ доставлен",
    }

    msg = STATUS_TEXT.get(courier_status)
    if not msg:
        return

    send_telegram_message(
        client_tg_id,
        msg + f"\n\n🧾 Заказ: {order.get('order_id')}"
    )

# delivery_fanout.py

import logging
from notifications import notify_kitchen_safe, notify_client_safe

log = logging.getLogger("delivery_fanout")


def fanout_delivery_status(
    order: dict,
    courier_status: str,
    kitchen_status: str,
):
    """
    Fan-out уведомлений по изменению доставки.
    Никаких исключений наружу.
    """
    try:
        # кухня всегда получает факт изменения
        notify_kitchen_safe(
            order,
            f"Статус доставки: {kitchen_status}",
        )

        # клиентские уведомления
        if kitchen_status == "delivery_in_progress":
            notify_client_safe(order, "🚚 Курьер выехал")

        if courier_status == "order_on_hands":
            notify_client_safe(order, "📦 Заказ у курьера")

        if kitchen_status == "delivered":
            notify_client_safe(
                order,
                "✅ Заказ доставлен",
                photo_file_id=order.get("proof_image_file_id"),
            )

    except Exception as e:
        log.exception(f"fanout failed: {e}")