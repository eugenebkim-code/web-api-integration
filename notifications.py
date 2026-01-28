# notifications.py

import logging
import requests
import os

log = logging.getLogger("notifications")

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_telegram_message(chat_id: int, text: str):
    """
    Безопасная отправка сообщения в Telegram.
    Никогда не кидает исключения наружу.
    """
    if not BOT_TOKEN or not chat_id:
        return

    try:
        resp = requests.post(
            TELEGRAM_API,
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=5,
        )

        if resp.status_code != 200:
            log.warning(
                f"Telegram send failed chat_id={chat_id} "
                f"status={resp.status_code} body={resp.text}"
            )

    except Exception as e:
        log.exception(f"Telegram send exception chat_id={chat_id}: {e}")

def notify_kitchen_safe(order: dict, text: str):
    try:
        # STUB: позже здесь будет реальный бот кухни
        print(f"[notify_kitchen] order={order['order_id']} | {text}")
    except Exception as e:
        log.warning(f"kitchen notify failed: {e}")


def notify_client_safe(order: dict, text: str, photo_file_id: str | None = None):
    try:
        # STUB: реальный бот курьерки будет тут
        msg = f"[notify_client] tg={order.get('client_tg_id')} | {text}"
        if photo_file_id:
            msg += f" | photo={photo_file_id}"
        print(msg)
    except Exception as e:
        order["last_client_notify_error"] = str(e)
        log.warning(f"client notify failed: {e}")