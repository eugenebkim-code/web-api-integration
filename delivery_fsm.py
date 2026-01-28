# delivery_fsm.py
# Каноничная FSM доставки — v1.0
# Источник истины для допустимых переходов состояний

from typing import Dict, Set

# Каноничные статусы доставки
DELIVERY_NEW = "delivery_new"
DELIVERY_IN_PROGRESS = "delivery_in_progress"
DELIVERED = "delivered"
CANCELLED = "cancelled"

# Финальные состояния
FINAL_STATES: Set[str] = {
    DELIVERED,
    CANCELLED,
}

# Таблица допустимых переходов
# ключ -> из какого статуса
# значение -> в какие можно перейти
TRANSITIONS: Dict[str, Set[str]] = {
    DELIVERY_NEW: {
        DELIVERY_NEW,              # idempotent
        DELIVERY_IN_PROGRESS,
        DELIVERED,                 # shortcut (курьер сразу закрыл)
        CANCELLED,
    },
    DELIVERY_IN_PROGRESS: {
        DELIVERY_IN_PROGRESS,      # idempotent
        DELIVERED,
        CANCELLED,
    },
    # финальные состояния:
    # разрешаем ТОЛЬКО idempotent
    DELIVERED: {
        DELIVERED,
    },
    CANCELLED: {
        CANCELLED,
    },
}


def is_valid_transition(current: str | None, incoming: str) -> bool:
    """
    Проверяет, допустим ли переход current -> incoming.

    current:
      - всегда должен быть установлен (delivery_new на старте)

    incoming:
      - каноничный статус доставки
    """
    if not current or not incoming:
        return False

    allowed = TRANSITIONS.get(current)
    if not allowed:
        return False

    return incoming in allowed


def is_final(status: str | None) -> bool:
    """
    Возвращает True, если статус финальный и immutable.
    """
    if not status:
        return False
    return status in FINAL_STATES