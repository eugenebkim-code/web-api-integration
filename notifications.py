# notifications.py

import logging
import requests
import os
from datetime import datetime
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
    """
    Уведомление кухни.
    Ошибки не ломают основной поток.
    """
    try:
        # STUB: позже реальный бот кухни
        print(f"[notify_kitchen] order={order.get('order_id')} | {text}")
    except Exception as e:
        log.warning(f"kitchen notify failed: {e}")


def notify_client_safe(
    order: dict,
    text: str,
    photo_file_id: str | None = None,
):
    """
    Fail-safe уведомление клиента.
    Web API не знает, доставлено сообщение или нет.
    """
    try:
        client_tg_id = order.get("client_tg_id")
        if not client_tg_id:
            order["last_client_notify_skipped"] = "no_client_tg_id"
            return

        payload = {
            "client_tg_id": client_tg_id,
            "text": text,
            "photo_file_id": photo_file_id,
            "ts": datetime.utcnow().isoformat(),
        }

        # STUB: имитация отправки
        print("[NOTIFY_CLIENT_STUB]", payload)

        # 🆕 ЯВНО фиксируем факт попытки уведомления
        order["last_client_notify_at"] = payload["ts"]
        order["last_client_notify_payload"] = payload

    except Exception as e:
        # ничего не ломаем
        order["last_client_notify_error"] = str(e)
        log.warning(f"client notify failed: {e}")