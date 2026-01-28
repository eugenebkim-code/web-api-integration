# sheets_sync.py

from datetime import datetime

ORDERS_SHEET = "orders"

# Колонки по твоей структуре
COL_DELIVERY_STATE = "T"
COL_COURIER_STATUS_RAW = "U"
COL_COURIER_EXTERNAL_ID = "V"
COL_COURIER_STATUS_DETAIL = "X"
COL_COURIER_LAST_ERROR = "Y"
COL_COURIER_SENT_AT = "Z"
COL_DELIVERY_CONFIRMED_AT = "AA"

def _norm(s: str) -> str:
    return (s or "").strip()

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
    print("[sheets] spreadsheet_id =", spreadsheet_id)
    print("[sheets] order_id =", order_id)

    try:
        rows = (
            sheets.values()
            .get(spreadsheetId=spreadsheet_id, range="orders!A:AA")
            .execute()
            .get("values", [])
        )

        if len(rows) < 2:
            print("[sheets] no data rows")
            return

        target_row = None
        needle = _norm(order_id)

        for idx, row in enumerate(rows[1:], start=2):
            cell = _norm(row[0]) if row else ""
            if cell == needle:
                target_row = idx
                break

        if not target_row:
            print(f"[sheets] order not found: {order_id}")
            return

        now = datetime.utcnow().isoformat()
        sent_at = courier_sent_at or now

        data = [
            {"range": f"{ORDERS_SHEET}!{COL_DELIVERY_STATE}{target_row}", "values": [[delivery_state]]},
            {"range": f"{ORDERS_SHEET}!{COL_COURIER_STATUS_RAW}{target_row}", "values": [[courier_status_raw]]},
            {"range": f"{ORDERS_SHEET}!{COL_COURIER_SENT_AT}{target_row}", "values": [[sent_at]]},
        ]

        if courier_external_id is not None:
            data.append(
                {"range": f"{ORDERS_SHEET}!{COL_COURIER_EXTERNAL_ID}{target_row}", "values": [[courier_external_id]]}
            )

        if courier_status_detail is not None:
            data.append(
                {"range": f"{ORDERS_SHEET}!{COL_COURIER_STATUS_DETAIL}{target_row}", "values": [[courier_status_detail]]}
            )

        if courier_last_error is not None:
            data.append(
                {"range": f"{ORDERS_SHEET}!{COL_COURIER_LAST_ERROR}{target_row}", "values": [[courier_last_error]]}
            )

        if delivery_confirmed_at is not None:
            data.append(
                {"range": f"{ORDERS_SHEET}!{COL_DELIVERY_CONFIRMED_AT}{target_row}", "values": [[delivery_confirmed_at]]}
            )

        sheets.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()

        print(f"[sheets] updated {order_id}")

    except Exception as e:
        print(f"[sheets] ERROR: {e}")
