import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def clear_orders():
    main.ORDERS.clear()
    yield
    main.ORDERS.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def headers_courier():
    return {"X-API-KEY": "DEV_KEY", "X-ROLE": "courier"}


@pytest.fixture
def headers_kitchen():
    return {"X-API-KEY": "DEV_KEY", "X-ROLE": "kitchen"}


def create_base_order(client, order_id="TEST_ORDER_001", with_delivery=True):
    payload = {
        "order_id": order_id,
        "source": "kitchen",
        "kitchen_id": 1,
        "client_tg_id": 111,
        "client_name": "Test",
        "client_phone": "010-0000",
        "pickup_address": "Kitchen addr",
        "delivery_address": "Client addr",
        "pickup_eta_at": "2026-01-27T15:40:00",
        "city": "dunpo",
    }

    client.post("/api/v1/orders", json=payload, headers={"X-API-KEY": "DEV_KEY", "X-ROLE": "kitchen"})

    if not with_delivery:
        main.ORDERS[order_id].pop("delivery_order_id", None)

    return order_id


def test_pickup_order_does_not_sync(client, headers_courier, monkeypatch):
    order_id = create_base_order(client, with_delivery=False)

    called = False

    def fake_sync(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "sync_delivery_status_to_kitchen", fake_sync)

    resp = client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "courier_assigned"},
        headers=headers_courier,
    )

    assert resp.status_code == 200
    assert called is False


def test_first_courier_status_triggers_sync(client, headers_courier, monkeypatch):
    order_id = create_base_order(client)

    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(main, "sync_delivery_status_to_kitchen", fake_sync)
    monkeypatch.setattr(main, "get_sheets_service_safe", lambda: None)

    client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "created"},
        headers=headers_courier,
    )

    assert len(calls) == 1
    assert calls[0]["courier_status_raw"] == "created"
    assert calls[0]["delivery_state"] == "delivery_new"


def test_delivered_sets_confirmed_once(client, headers_courier, monkeypatch):
    order_id = create_base_order(client)

    monkeypatch.setattr(main, "sync_delivery_status_to_kitchen", lambda **kw: None)
    monkeypatch.setattr(main, "get_sheets_service_safe", lambda: None)

    client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "delivered"},
        headers=headers_courier,
    )

    first_ts = main.ORDERS[order_id].get("delivery_confirmed_at")
    assert first_ts is not None

    client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "delivered"},
        headers=headers_courier,
    )

    second_ts = main.ORDERS[order_id].get("delivery_confirmed_at")
    assert first_ts == second_ts


def test_unknown_status_is_safe(client, headers_courier, monkeypatch):
    order_id = create_base_order(client)

    monkeypatch.setattr(main, "sync_delivery_status_to_kitchen", lambda **kw: None)
    monkeypatch.setattr(main, "get_sheets_service_safe", lambda: None)

    resp = client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "weird_status"},
        headers=headers_courier,
    )

    assert resp.status_code == 200
    assert "courier_last_error" in main.ORDERS[order_id]


def test_idempotent_status(client, headers_courier, monkeypatch):
    order_id = create_base_order(client)

    calls = []

    monkeypatch.setattr(main, "sync_delivery_status_to_kitchen", lambda **kw: calls.append(kw))
    monkeypatch.setattr(main, "get_sheets_service_safe", lambda: None)

    client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "courier_assigned"},
        headers=headers_courier,
    )

    client.post(
        f"/api/v1/orders/{order_id}/status",
        json={"status": "courier_assigned"},
        headers=headers_courier,
    )

    assert len(calls) == 1


def test_order_id_strip():
    from sheets_sync import sync_delivery_status_to_kitchen

    rows = [
        ["order_id"],
        [" TEST_ORDER_001 "],
    ]

    class FakeExec:
        def __init__(self, payload):
            self.payload = payload
        def execute(self):
            return self.payload

    class FakeValues:
        def get(self, *a, **k):
            return FakeExec({"values": rows})
        def batchUpdate(self, *a, **k):
            return FakeExec({})

    class FakeSheets:
        def values(self):
            return FakeValues()

    fake_sheets = FakeSheets()

    sync_delivery_status_to_kitchen(
        sheets=fake_sheets,
        spreadsheet_id="X",
        order_id="TEST_ORDER_001",
        delivery_state="delivery_new",
        courier_status_raw="created",
        courier_external_id="CID",
    )
