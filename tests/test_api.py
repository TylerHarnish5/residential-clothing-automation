from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from backend.api import app, get_session
from backend.db_models import Base


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_order_context(client: TestClient) -> tuple[str, str]:
    facility = client.post(
        "/facilities", json={"name": "Maple House", "shipping_address": "1 Maple Road"}
    )
    assert facility.status_code == 201
    facility_id = facility.json()["id"]

    requester = client.post(
        "/users",
        json={
            "full_name": "Fran Facility Staff",
            "role": "authorized_requester",
            "facility_id": facility_id,
        },
    )
    assert requester.status_code == 201

    resident = client.post(
        "/residents", json={"facility_id": facility_id, "full_name": "Ada Resident"}
    )
    assert resident.status_code == 201
    return resident.json()["id"], requester.json()["id"]


def create_product(client: TestClient, *, sku: str = "SHIRT-M-BLU", price: str = "25.00") -> None:
    response = client.post(
        "/products",
        json={"sku": sku, "name": "Blue Shirt", "size": "M", "unit_price": price},
    )
    assert response.status_code == 201


def test_create_list_and_retrieve_products(client: TestClient) -> None:
    create_product(client)

    listed = client.get("/products")
    retrieved = client.get("/products/SHIRT-M-BLU")

    assert listed.status_code == 200
    assert listed.json()[0]["sku"] == "SHIRT-M-BLU"
    assert retrieved.status_code == 200
    assert retrieved.json()["unit_price"] == "25.00"


def test_duplicate_product_sku_returns_conflict(client: TestClient) -> None:
    create_product(client)

    duplicate = client.post(
        "/products",
        json={"sku": "SHIRT-M-BLU", "name": "Second Shirt", "size": "L", "unit_price": "20.00"},
    )

    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_draft_order_items_confirmation_and_retrieval(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    create_product(client)
    created = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )
    assert created.status_code == 201
    order_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    item_added = client.post(
        f"/orders/{order_id}/items", json={"product_sku": "SHIRT-M-BLU", "quantity": 2}
    )
    assert item_added.status_code == 200
    assert item_added.json()["items"][0]["unit_price"] == "25.00"
    assert item_added.json()["total"] == "50.00"

    confirmed = client.post(f"/orders/{order_id}/confirm", json={"shipping_cost": "10.00"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "pending_approval"
    assert confirmed.json()["invoice"]["total"] == "60.00"

    retrieved = client.get(f"/orders/{order_id}")
    assert retrieved.status_code == 200
    assert retrieved.json()["resident_name"] == "Ada Resident"
    assert retrieved.json()["shipping_address"] == "1 Maple Road"


def test_item_that_exceeds_order_budget_returns_bad_request(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    create_product(client, sku="COAT-M-NVY", price="60.00")
    order = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )

    response = client.post(
        f"/orders/{order.json()['id']}/items",
        json={"product_sku": "COAT-M-NVY", "quantity": 2},
    )

    assert response.status_code == 400
    assert "exceed the order budget" in response.json()["detail"]


def test_unknown_product_returns_not_found(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    order = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )

    response = client.post(
        f"/orders/{order.json()['id']}/items",
        json={"product_sku": "MISSING-SKU", "quantity": 1},
    )

    assert response.status_code == 404
    assert "Unknown product" in response.json()["detail"]


def test_confirming_or_editing_a_non_draft_order_returns_conflict(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    create_product(client)
    order = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )
    order_id = order.json()["id"]
    client.post(f"/orders/{order_id}/items", json={"product_sku": "SHIRT-M-BLU", "quantity": 1})
    assert client.post(f"/orders/{order_id}/confirm", json={"shipping_cost": "5.00"}).status_code == 200

    second_confirmation = client.post(f"/orders/{order_id}/confirm", json={"shipping_cost": "5.00"})
    add_after_confirmation = client.post(
        f"/orders/{order_id}/items", json={"product_sku": "SHIRT-M-BLU", "quantity": 1}
    )

    assert second_confirmation.status_code == 409
    assert add_after_confirmation.status_code == 409


def test_boss_can_approve_a_confirmed_order(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    boss = client.post("/users", json={"full_name": "Bob Boss", "role": "boss"})
    assert boss.status_code == 201
    create_product(client)
    order = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )
    order_id = order.json()["id"]
    client.post(f"/orders/{order_id}/items", json={"product_sku": "SHIRT-M-BLU", "quantity": 1})
    client.post(f"/orders/{order_id}/confirm", json={"shipping_cost": "5.00"})

    approved = client.post(
        f"/orders/{order_id}/approve",
        json={"boss_id": boss.json()["id"], "allow_partial_fulfillment": True},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_inactive_product_blocks_confirmation(client: TestClient) -> None:
    resident_id, requester_id = create_order_context(client)
    inactive_product = client.post(
        "/products",
        json={
            "sku": "RETIRED-M-GRY",
            "name": "Retired Shirt",
            "size": "M",
            "unit_price": "25.00",
            "is_active": False,
        },
    )
    assert inactive_product.status_code == 201
    order = client.post(
        "/orders",
        json={"resident_id": resident_id, "requester_id": requester_id, "budget_amount": "100.00"},
    )
    order_id = order.json()["id"]
    assert client.post(
        f"/orders/{order_id}/items", json={"product_sku": "RETIRED-M-GRY", "quantity": 1}
    ).status_code == 200

    response = client.post(f"/orders/{order_id}/confirm", json={"shipping_cost": "5.00"})

    assert response.status_code == 400
    assert "unavailable" in response.json()["detail"]
