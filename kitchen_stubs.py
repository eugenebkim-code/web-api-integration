from typing import Dict, List
import logging

from kitchen_context import require

log = logging.getLogger("catalog")


def read_kitchen_catalog(
    sheets,
    kitchen_id: str,
) -> Dict[str, List[dict]]:
    """
    MVP-костыль чтения каталога кухни.
    Читаем ТОЛЬКО customer_price (колонка M).
    """

    log.info(f"[CATALOG] start kitchen={kitchen_id}")

    kitchen = require(kitchen_id)
    spreadsheet_id = kitchen.spreadsheet_id

    result = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range="products!A2:M",
    ).execute()

    rows = result.get("values", [])
    log.info(f"[CATALOG] rows={len(rows)}")

    products: List[dict] = []
    categories_map: Dict[str, dict] = {}

    for idx, row in enumerate(rows, start=2):
        try:
            product_id = row[0].strip()
            name = row[1].strip()

            # C owner_price нам НЕ нужен
            # D available
            available_raw = row[3] if len(row) > 3 else "TRUE"
            available = str(available_raw).lower() not in ("false", "0", "no")

            # E category
            category = row[4].strip()

            # M customer_price (index 12)
            raw_price = row[12] if len(row) > 12 else None
            if raw_price in (None, "", "0"):
                raise ValueError("empty customer_price")

            price = int(
                float(
                    str(raw_price)
                    .replace("₩", "")
                    .replace(",", "")
                    .strip()
                )
            )

            photo_url = row[7] if len(row) > 7 else None

        except Exception as e:
            log.warning(f"[CATALOG] skip row {idx}: {e}")
            continue

        products.append(
            {
                "id": product_id,
                "category_id": category,
                "name": name,
                "price": price,
                "available": available,
                "photo_url": photo_url,
            }
        )

        if category not in categories_map:
            categories_map[category] = {
                "id": category,
                "name": category,
            }

    log.info(
        f"[CATALOG] products={len(products)} categories={len(categories_map)}"
    )

    return {
        "categories": list(categories_map.values()),
        "products": products,
    }