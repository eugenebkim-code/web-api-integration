#sheets_sync.py

from datetime import datetime

ORDERS_SHEET = "orders"

# === Колонки (зафиксированы контрактом) ===
COL_DELIVERY_STATE = "T"
COL_COURIER_STATUS_RAW = "U"
COL_COURIER_EXTERNAL_ID = "V"
COL_COURIER_STATUS_DETAIL = "X"
COL_COURIER_LAST_ERROR = "Y"
COL_COURIER_SENT_AT = "Z"
COL_DELIVERY_CONFIRMED_AT = "AA"


def _norm(s: str | None) -> str:
    return (s or "").strip()

def map_courier_status_to_delivery_state(courier_status_raw: str) -> str | None:
    """
    Stable mapping from courier raw status to kitchen delivery_state.
    Unknown statuses MUST NOT break the flow.
    """

    mapping = {
        "created": "courier_requested",
        "courier_requested": "courier_requested",
        "courier_assigned": "courier_assigned",
        "courier_departed": "delivery_in_progress",
        "courier_delivered": "delivered",
        "rejected": "rejected",
    }

    return mapping.get((courier_status_raw or "").strip())

def sync_delivery_status_to_kitchen(
    sheets,
    spreadsheet_id: str,
    order_id: str,
    delivery_state: str,
    courier_status_raw: str,
    courier_external_id: str | None = None,
    courier_status_detail: str | None = None,
    courier_last_error: str | None = None,
    courier_sent_at: str | None = None,
    delivery_confirmed_at: str | None = None,
):
    """
    Sync delivery-related status into kitchen orders sheet.

    Writes ONLY delivery-related columns.
    Legacy fields are intentionally untouched.

    Source of truth: Courierka
    """

    try:
        rows = (
            sheets.values()
            .get(spreadsheetId=spreadsheet_id, range=f"{ORDERS_SHEET}!A:AD")
            .execute()
            .get("values", [])
        )

        if len(rows) < 2:
            return

        needle = _norm(order_id)
        target_row = None
        existing_row = None

        for idx, row in enumerate(rows[1:], start=2):
            cell = _norm(row[0]) if row else ""
            if cell == needle:
                target_row = idx
                existing_row = row
                break

        if not target_row:
            log.error(
                "[sheets_sync] order_id=%s not found in orders sheet, skip update",
                order_id
            )
            return

        now = datetime.utcnow().isoformat()

        # existing values (если строки короче — считаем пустыми)
        def safe_cell(row, index):
            return row[index] if len(row) > index else ""

        existing_sent_at = _norm(safe_cell(existing_row, 25))   # Z
        existing_confirmed = _norm(safe_cell(existing_row, 26)) # AA

        data = []

        # delivery_state
        data.append({
            "range": f"{ORDERS_SHEET}!{COL_DELIVERY_STATE}{target_row}",
            "values": [[delivery_state]],
        })

        # courier_status_raw
        data.append({
            "range": f"{ORDERS_SHEET}!{COL_COURIER_STATUS_RAW}{target_row}",
            "values": [[courier_status_raw]],
        })

        # courier_sent_at — ТОЛЬКО первый раз
        if not existing_sent_at:
            data.append({
                "range": f"{ORDERS_SHEET}!{COL_COURIER_SENT_AT}{target_row}",
                "values": [[courier_sent_at or now]],
            })

        if courier_external_id is not None:
            data.append({
                "range": f"{ORDERS_SHEET}!{COL_COURIER_EXTERNAL_ID}{target_row}",
                "values": [[courier_external_id]],
            })

        if courier_status_detail is not None:
            data.append({
                "range": f"{ORDERS_SHEET}!{COL_COURIER_STATUS_DETAIL}{target_row}",
                "values": [[courier_status_detail]],
            })

        if courier_last_error is not None:
            data.append({
                "range": f"{ORDERS_SHEET}!{COL_COURIER_LAST_ERROR}{target_row}",
                "values": [[courier_last_error]],
            })

        # delivery_confirmed_at — ТОЛЬКО один раз
        if delivery_confirmed_at and not existing_confirmed:
            data.append({
                "range": f"{ORDERS_SHEET}!{COL_DELIVERY_CONFIRMED_AT}{target_row}",
                "values": [[delivery_confirmed_at]],
            })

        if not data:
            return

        sheets.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": data,
            },
        ).execute()

    except Exception as e:
        # fail-safe: не ломаем основной поток
        print(f"[sheets_sync] ERROR: {e}")
