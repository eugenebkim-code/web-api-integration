# kitchen_context.py
"""
Kitchen Context Layer

Назначение:
- Надстройка мультикухонности
- Единая точка доступа к данным кухни
- Изоляция данных и денег
- Подготовка к масштабированию

Источник истины:
- Kitchen Registry (Google Sheets)

ВАЖНО:
- Бизнес-логики здесь нет
- Заказы здесь НЕ создаются
- Статусы здесь НЕ меняются
"""

from dataclasses import dataclass
from typing import Dict, Optional, Set, Any
from datetime import datetime, timedelta
import logging

log = logging.getLogger("KITCHEN_CONTEXT")


# =========================
# Exceptions
# =========================

class KitchenContextError(Exception):
    pass


class KitchenNotFound(KitchenContextError):
    pass


class KitchenInactive(KitchenContextError):
    pass


class ActionNotAllowed(KitchenContextError):
    pass


class RegistryNotLoaded(KitchenContextError):
    pass


# =========================
# Data model
# =========================

@dataclass(frozen=True)
class KitchenContext:
    """
    Immutable-контекст кухни.
    Все остальные части системы обязаны
    получать данные ТОЛЬКО отсюда.
    """
    kitchen_id: int
    status: str

    spreadsheet_id: str

    owner_chat_id: int
    staff_chat_ids: Set[int]

    city: str
    timezone: Optional[str]

    commission_pct: float

    enabled_actions: Set[str]
    theme: Dict[str, Any]


# =========================
# Internal state (cache)
# =========================

_REGISTRY: Dict[str, KitchenContext] = {}
_LAST_LOADED_AT: Optional[datetime] = None
_REGISTRY_TTL = timedelta(hours=1)


# =========================
# Registry loading (skeleton)
# =========================

def load_registry(force: bool = False) -> None:
    """
    Загружает Kitchen Registry в память.

    В v1:
    - вызывается при старте
    - данные читаются из Google Sheets
    - кэшируются на TTL

    Реализация чтения из Sheets
    будет добавлена отдельно.
    """
    global _REGISTRY, _LAST_LOADED_AT

    now = datetime.utcnow()

    if (
        not force
        and _LAST_LOADED_AT
        and now - _LAST_LOADED_AT < _REGISTRY_TTL
    ):
        return

    log.info("Loading Kitchen Registry...")

    # TODO:
    # 1. Прочитать registry sheet
    # 2. Провалидировать строки
    # 3. Собрать KitchenContext
    # 4. Заполнить _REGISTRY

    _REGISTRY = {
        "kitchen_1": KitchenContext(
            kitchen_id="kitchen_1",
            status="active",
            spreadsheet_id="1dQFxRHsS2yFSV5rzB_q4q5WLv2GPaB2Gyawm2ZudPx4",
            owner_chat_id=2115245228,
            staff_chat_ids={2115245228},
            city="dunpo",
            timezone=None,
            commission_pct=0.0,
            enabled_actions={
                "geo:validate",
                "order:create",
            },
            theme={},
        ),

        "kitchen_2": KitchenContext(
            kitchen_id="kitchen_2",
            status="active",
            spreadsheet_id="1oAFB9Xihqbdph217AEfXlPNTjuZVAlBr7UU4JDOmygQ",
            owner_chat_id=2115245228,
            staff_chat_ids={2115245228},
            city="dunpo",
            timezone=None,
            commission_pct=0.0,
            enabled_actions={
                "geo:validate",
                "order:create",
            },
            theme={},
        ),

        "kitchen_3": KitchenContext(
            kitchen_id="kitchen_3",
            status="active",
            spreadsheet_id="1IUPf2cExtl2IyikgglEGIDE6tTVVd8B5lpaMee-U6GE",
            owner_chat_id=2115245228,
            staff_chat_ids={2115245228},
            city="dunpo",
            timezone=None,
            commission_pct=0.0,
            enabled_actions={
                "geo:validate",
                "order:create",
            },
            theme={},
        ),

        "kitchen_4": KitchenContext(
            kitchen_id="kitchen_4",
            status="active",
            spreadsheet_id="1xjK95TRI4s-Q_5UuqEnpY0nKonhtg1qdsppNdcx9jHQ",
            owner_chat_id=2115245228,
            staff_chat_ids={2115245228},
            city="dunpo",
            timezone=None,
            commission_pct=0.0,
            enabled_actions={
                "geo:validate",
                "order:create",
            },
            theme={},
        ),

        "kitchen_5": KitchenContext(
            kitchen_id="kitchen_5",
            status="active",
            spreadsheet_id="1aLAOt31_sR6POGxqfq3ouAqoMt2dyBjw80908SZFF_Q",
            owner_chat_id=2115245228,
            staff_chat_ids={2115245228},
            city="dunpo",
            timezone=None,
            commission_pct=0.0,
            enabled_actions={
                "geo:validate",
                "order:create",
            },
            theme={},
        ),
    }
    
    _LAST_LOADED_AT = datetime.utcnow()

    log.info("Kitchen Registry loaded")


def reload_registry() -> None:
    """
    Принудительная перезагрузка Registry.
    Используется в админских сценариях.
    """
    load_registry(force=True)


# =========================
# Public API
# =========================

def require(kitchen_id: str) -> KitchenContext:
    """
    Получить KitchenContext.

    Если:
    - registry не загружен
    - кухня не найдена
    - кухня не active

    → бросаем исключение.
    """

    if not _REGISTRY:
        raise RegistryNotLoaded("Kitchen Registry is not loaded")

    kitchen = _REGISTRY.get(kitchen_id)

    if not kitchen:
        raise KitchenNotFound(f"Kitchen not found: {kitchen_id}")

    if kitchen.status != "active":
        raise KitchenInactive(
            f"Kitchen {kitchen_id} is not active (status={kitchen.status})"
        )

    return kitchen


def get(kitchen_id: str) -> Optional[KitchenContext]:
    """
    Мягкое получение контекста.
    Используется для диагностики.
    """
    return _REGISTRY.get(kitchen_id)


def assert_action_allowed(kitchen: KitchenContext, action: str) -> None:
    """
    Проверка разрешения action для кухни.
    """

    if action not in kitchen.enabled_actions:
        raise ActionNotAllowed(
            f"Action '{action}' is not allowed for kitchen '{kitchen.kitchen_id}'"
        )


# =========================
# Helpers (часто используемые)
# =========================

def is_staff(kitchen: KitchenContext, user_id: int) -> bool:
    return user_id in kitchen.staff_chat_ids or user_id == kitchen.owner_chat_id


def is_owner(kitchen: KitchenContext, user_id: int) -> bool:
    return user_id == kitchen.owner_chat_id


def list_kitchens() -> Set[int]:
    """
    Для диагностики и логов.
    """
    return set(_REGISTRY.keys())


def registry_info() -> Dict[str, Any]:
    """
    Краткая информация о состоянии Registry.
    """
    return {
        "loaded": bool(_REGISTRY),
        "kitchens": len(_REGISTRY),
        "last_loaded_at": _LAST_LOADED_AT.isoformat() if _LAST_LOADED_AT else None,
        "ttl_seconds": int(_REGISTRY_TTL.total_seconds()),
    }
