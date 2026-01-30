#delivery_fanout.py

import logging
from notifications import notify_kitchen_safe, notify_client_safe

log = logging.getLogger("delivery_fanout")


def fanout_delivery_status(
    *,
    order: dict,
    courier_status: str,
    kitchen_status: str,
):
    """
    Единственная точка fan-out уведомлений о доставке.
    Web API сообщает ФАКТЫ, не UI.
    """

    try:
        order_id = order.get("order_id")

        # --- кухня всегда получает факт изменения ---
        notify_kitchen_safe(
            order,
            f"Заказ {order_id}\nСтатус доставки: {kitchen_status}",
        )

        # --- клиентские уведомления ---
        if courier_status == "courier_assigned":
            notify_client_safe(
                order,
                "🚚 Курьер назначен. Мы готовимся к доставке.",
            )

        elif courier_status == "courier_departed":
            notify_client_safe(
                order,
                "🚚 Курьер выехал.",
            )

        elif courier_status == "order_on_hands":
            notify_client_safe(
                order,
                "📦 Заказ забран курьером.",
            )

        elif kitchen_status == "delivered":
            notify_client_safe(
                order,
                "✅ Заказ доставлен.",
                photo_file_id=order.get("proof_image_file_id"),
            )

    except Exception as e:
        # fail-safe: уведомления не ломают основной поток
        log.exception(f"fanout_delivery_status failed: {e}")
