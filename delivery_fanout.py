import logging
from notifications import notify_kitchen_safe, notify_client_safe

log = logging.getLogger("delivery_fanout")


def fanout_delivery_status(
    order: dict,
    courier_status: str,
    kitchen_status: str,
):
    """
    Fan-out событий доставки.
    """

    try:
        order_id = order.get("order_id")

        # ==============================
        # 🟢 УВЕДОМЛЕНИЕ КУХНИ (ОСНОВНОЕ)
        # ==============================

        if order.get("kitchen_tg_chat_id"):
            notify_kitchen_safe(
                order=order,
                text=(
                    f"🍽 Заказ {order_id}\n"
                    f"Статус доставки: {kitchen_status}"
                ),
                photo_file_id=order.get("proof_image_file_id"),
            )
        else:
            log.info(
                "[FANOUT] kitchen_tg_chat_id missing | order_id=%s",
                order_id,
            )

        # ==============================
        # 🟡 КЛИЕНТ (STUB)
        # ==============================

        if courier_status == "courier_assigned":
            notify_client_safe(order, "🚚 Курьер назначен.")

        elif courier_status == "courier_departed":
            notify_client_safe(order, "🚚 Курьер выехал.")

        elif courier_status == "order_on_hands":
            notify_client_safe(order, "📦 Заказ у курьера.")

        elif kitchen_status == "delivered":
            notify_client_safe(
                order,
                "✅ Заказ доставлен.",
                photo_file_id=order.get("proof_image_file_id"),
            )

    except Exception as e:
        order["fanout_last_error"] = str(e)
        log.exception(
            "[FANOUT_FAILED] order_id=%s",
            order.get("order_id"),
        )
