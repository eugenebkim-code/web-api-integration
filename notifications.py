import os
import logging
from datetime import datetime
import httpx

log = logging.getLogger("webapi.notify")

TG_BOT_TOKEN = os.getenv("KITCHEN_BOT_TOKEN")
TG_API_BASE = "https://api.telegram.org/bot"


# =========================
# CLIENT NOTIFICATIONS (STUB)
# =========================

def notify_client_safe(
    order: dict,
    text: str,
    photo_file_id: str | None = None,
):
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

        print("[NOTIFY_CLIENT_STUB]", payload)

        order["last_client_notify_at"] = payload["ts"]
        order["last_client_notify_payload"] = payload

    except Exception as e:
        order["last_client_notify_error"] = str(e)
        log.warning("[CLIENT_NOTIFY_FAILED] %s", e)


# =========================
# KITCHEN NOTIFICATIONS
# =========================

def notify_kitchen_safe(
    order: dict,
    text: str,
    chat_id: int | None = None,
    photo_file_id: str | None = None,
):
    try:
        kitchen_chat_id = chat_id or order.get("kitchen_tg_chat_id")
        if not kitchen_chat_id:
            log.warning(
                "[NOTIFY_KITCHEN_SKIP] no chat_id | order_id=%s",
                order.get("order_id"),
            )
            return

        if order.get("eta_minutes"):
            text += f"\n⏱ Готовность через {order['eta_minutes']} мин"

        if photo_file_id:
            sent = tg_send_photo(
                chat_id=kitchen_chat_id,
                photo_file_id=photo_file_id,
                caption=text,
            )

            if not sent:
                log.warning(
                    "[notify_kitchen] failed to send photo, fallback to text | order_id=%s",
                    order.get("order_id"),
                )
                tg_send_message(
                    chat_id=kitchen_chat_id,
                    text=text,
                )
        else:
            tg_send_message(
                chat_id=kitchen_chat_id,
                text=text,
            )

    except Exception as e:
        log.exception(
            "[NOTIFY_KITCHEN_FAILED] order_id=%s",
            order.get("order_id"),
        )


# =========================
# TELEGRAM LOW-LEVEL
# =========================

def tg_send_message(chat_id: int, text: str):
    if not TG_BOT_TOKEN:
        log.error("KITCHEN_BOT_TOKEN is not set")
        return

    url = f"{TG_API_BASE}{TG_BOT_TOKEN}/sendMessage"
    with httpx.Client(timeout=10) as client:
        r = client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
            },
        )
        r.raise_for_status()


def tg_send_photo(
    chat_id: int,
    photo_file_id: str | None,
    caption: str | None = None,
) -> bool:
    if not TG_BOT_TOKEN:
        log.error("KITCHEN_BOT_TOKEN is not set")
        return False

    if not photo_file_id:
        log.warning(
            "[tg_send_photo] skip send: empty photo_file_id chat=%s",
            chat_id,
        )
        return False

    url = f"{TG_API_BASE}{TG_BOT_TOKEN}/sendPhoto"

    payload = {
        "chat_id": chat_id,
        "photo": photo_file_id,
    }

    # caption добавляем ТОЛЬКО если он реально есть
    if caption:
        payload["caption"] = caption

    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(url, json=payload)

        if r.status_code != 200:
            log.error(
                "[tg_send_photo] telegram error chat=%s status=%s response=%s payload=%s",
                chat_id,
                r.status_code,
                r.text,
                payload,
            )
            return False

        log.info(
            "[tg_send_photo] sent photo chat=%s file_id=%s",
            chat_id,
            photo_file_id,
        )
        return True

    except Exception:
        log.exception(
            "[tg_send_photo] exception while sending photo chat=%s file_id=%s",
            chat_id,
            photo_file_id,
        )
        return False


    except httpx.HTTPStatusError as e:
        # 🔥 ПАТЧ 2.1: не роняем уведомления кухни
        log.exception(
            "[tg_send_photo] sendPhoto failed, fallback to text | chat=%s file_id=%s",
            chat_id,
            photo_file_id,
        )

        # fallback: обычное сообщение
        fallback_url = f"{TG_API_BASE}{TG_BOT_TOKEN}/sendMessage"
        fallback_text = caption or "📦 Фото доставки"

        try:
            with httpx.Client(timeout=10) as client:
                client.post(
                    fallback_url,
                    json={
                        "chat_id": chat_id,
                        "text": fallback_text,
                    },
                )
        except Exception:
            log.exception("[tg_send_photo] fallback sendMessage also failed")
