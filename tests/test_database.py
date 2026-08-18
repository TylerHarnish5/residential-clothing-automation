import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import (
    create_database_engine,
    create_session_factory,
    get_database_url,
)
from backend.db_models import (
    Base,
    ChangeRequestStatus,
    ChangeRequestType,
    FacilityModel,
    InventoryItemModel,
    OrderModel,
    ProductModel,
    ResidentModel,
    ShipmentStatus,
    UserModel,
    UserRole,
)
from backend.orders import Order, OrderStatus, Product
from backend.repositories import (
    InventoryRepository,
    OrderRepository,
    OrderWorkflowRepository,
    ProductRepository,
    ShipmentRepository,
)


@pytest.fixture
def session() -> Session:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as database_session:
        yield database_session
        database_session.rollback()
    engine.dispose()


@pytest.fixture
def facility_with_people(session: Session) -> tuple[ResidentModel, UserModel, UserModel]:
    facility = FacilityModel(name="Maple House", shipping_address="1 Maple Road")
    resident = ResidentModel(facility=facility, full_name="Ada Resident")
    requester = UserModel(
        facility=facility,
        full_name="Fran Facility Staff",
        role=UserRole.AUTHORIZED_REQUESTER.value,
    )
    boss = UserModel(full_name="Bob Boss", role=UserRole.BOSS.value)
    session.add_all([facility, resident, requester, boss])
    session.flush()
    return resident, requester, boss


def test_order_round_trip_preserves_invoice_and_snapshots(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("SHIRT-M-BLU", "Blue Shirt", "M", "25.00")
    product_model = ProductRepository().add(session, product)
    order = Order("100.00")
    order.add_item(product, quantity=2)
    order.submit_for_approval("10.00")
    order.approve(allow_partial_fulfillment=True)

    stored_order = OrderRepository().add(
        session, order, resident=resident, requester=requester, boss=boss
    )
    product_model.name = "Renamed Shirt"
    product_model.unit_price = Decimal("30.00")
    resident.full_name = "Ada New Name"
    resident.facility.shipping_address = "2 New Road"
    session.commit()

    restored = OrderRepository().get_domain_order(session, stored_order.id)
    stored_line = stored_order.items[0]

    assert restored.total == Decimal("60.00")
    assert restored.items[0].product_name == "Blue Shirt"
    assert restored.items[0].unit_price == Decimal("25.00")
    assert stored_order.resident_name_snapshot == "Ada Resident"
    assert stored_order.shipping_address_snapshot == "1 Maple Road"
    assert stored_line.product_name_snapshot == "Blue Shirt"
    assert stored_order.invoice is not None
    assert stored_order.invoice.total == Decimal("60.00")
    assert stored_order.approvals[0].boss_id == boss.id


def test_repository_rejects_an_invoice_that_exceeds_its_order_budget(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, _boss = facility_with_people
    product = Product("PANTS-M-BLK", "Black Pants", "M", "50.00")
    ProductRepository().add(session, product)
    order = Order("55.00")
    order.add_item(product)
    order.shipping_cost = Decimal("10.00")
    order.status = order.status.PENDING_APPROVAL

    with pytest.raises(ValueError, match="exceeds the order budget"):
        OrderRepository().add(session, order, resident=resident, requester=requester)


def test_inventory_reservation_persists_and_cannot_exceed_available_stock(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("SOCKS-ONE-WHT", "White Socks", "One Size", "5.00")
    product_model = ProductRepository().add(session, product)
    order = Order("100.00")
    order.add_item(product, quantity=3)
    order.submit_for_approval("5.00")
    order.approve(allow_partial_fulfillment=True)
    order.approved_at = datetime.now(timezone.utc)
    stored_order = OrderRepository().add(
        session, order, resident=resident, requester=requester, boss=boss
    )

    inventory_repository = InventoryRepository()
    inventory = inventory_repository.stock_product(session, product_model, quantity=2)
    reservation = inventory_repository.reserve(session, stored_order.items[0], quantity=2)

    assert reservation.quantity == 2
    assert inventory.reserved_quantity == 2

    with pytest.raises(ValueError, match="available inventory"):
        inventory_repository.reserve(session, stored_order.items[0], quantity=1)


def test_database_inventory_constraint_rejects_more_reserved_than_stock(session: Session) -> None:
    product = Product("HAT-ONE-GRY", "Grey Hat", "One Size", "10.00")
    product_model = ProductRepository().add(session, product)
    session.add(
        InventoryItemModel(
            product=product_model,
            quantity_on_hand=1,
            reserved_quantity=2,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_database_url_reads_an_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = "postgresql+psycopg://user:password@localhost:5432/test_database"
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert get_database_url() == database_url


def pending_order(session: Session, product: Product, resident: ResidentModel, requester: UserModel):
    product_repository = ProductRepository()
    try:
        product_repository.get_domain_product(session, product.sku)
    except ValueError:
        product_repository.add(session, product)
    order = Order("500.00")
    order.add_item(product, quantity=2)
    order.submit_for_approval("5.00")
    return OrderRepository().add(session, order, resident=resident, requester=requester)


def test_boss_approval_revalidates_product_availability_and_records_decision(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("COAT-M-NVY", "Navy Coat", "M", "50.00")
    stored_order = pending_order(session, product, resident, requester)
    stored_order.items[0].product.is_active = False

    with pytest.raises(ValueError, match="unavailable"):
        OrderWorkflowRepository().approve_order(
            session, stored_order.id, boss=boss, allow_partial_fulfillment=True
        )

    stored_order.items[0].product.is_active = True
    approved = OrderWorkflowRepository().approve_order(
        session, stored_order.id, boss=boss, allow_partial_fulfillment=True
    )

    assert approved.status == OrderStatus.APPROVED.value
    assert approved.approved_at is not None
    assert approved.approvals[0].decision == "approved"


def test_requester_must_belong_to_the_residents_facility(session: Session) -> None:
    first_facility = FacilityModel(name="First", shipping_address="1 First Street")
    second_facility = FacilityModel(name="Second", shipping_address="2 Second Street")
    resident = ResidentModel(facility=first_facility, full_name="Resident")
    requester = UserModel(
        facility=second_facility,
        full_name="Wrong Facility Requester",
        role=UserRole.AUTHORIZED_REQUESTER.value,
    )
    session.add_all([first_facility, second_facility, resident, requester])
    session.flush()
    product = Product("SWEATER-M-GRN", "Green Sweater", "M", "30.00")
    ProductRepository().add(session, product)
    order = Order("100.00")
    order.add_item(product)

    with pytest.raises(ValueError, match="resident's facility"):
        OrderRepository().add(session, order, resident=resident, requester=requester)


def test_approved_cancellation_releases_persisted_inventory_reservations(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("PANTS-M-GRY", "Grey Pants", "M", "40.00")
    stored_order = pending_order(session, product, resident, requester)
    workflow = OrderWorkflowRepository()
    workflow.approve_order(session, stored_order.id, boss=boss, allow_partial_fulfillment=False)
    inventory = InventoryRepository()
    inventory.stock_product(session, stored_order.items[0].product, 2)
    inventory.reserve(session, stored_order.items[0], 2)

    change_request = workflow.request_change(
        session,
        stored_order.id,
        requester=requester,
        request_type=ChangeRequestType.CANCELLATION,
    )
    decided = workflow.decide_change_request(
        session, change_request.id, boss=boss, approved=True
    )

    assert decided.status == ChangeRequestStatus.APPROVED.value
    assert stored_order.status == OrderStatus.CANCELLED.value
    assert stored_order.items[0].product.inventory_item.reserved_quantity == 0


def test_database_allocation_prioritizes_earlier_boss_approval(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("SHIRT-L-RED", "Red Shirt", "L", "20.00")
    first = pending_order(session, product, resident, requester)
    second = pending_order(session, product, resident, requester)
    workflow = OrderWorkflowRepository()
    workflow.approve_order(session, first.id, boss=boss, allow_partial_fulfillment=False)
    workflow.approve_order(session, second.id, boss=boss, allow_partial_fulfillment=True)
    first.approved_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    second.approved_at = datetime.now(timezone.utc)
    InventoryRepository().stock_product(session, first.items[0].product, 3)

    allocations = InventoryRepository().allocate_approved_orders(session)
    allocations_by_order = {allocation.order_id: allocation for allocation in allocations}

    assert allocations_by_order[first.id].line_allocations[0].reserved_quantity == 2
    assert allocations_by_order[second.id].line_allocations[0].reserved_quantity == 1


def test_shipment_requires_reserved_stock_and_reaches_ready_to_ship(
    session: Session, facility_with_people: tuple[ResidentModel, UserModel, UserModel]
) -> None:
    resident, requester, boss = facility_with_people
    product = Product("SOCKS-ONE-NVY", "Navy Socks", "One Size", "5.00")
    stored_order = pending_order(session, product, resident, requester)
    workflow = OrderWorkflowRepository()
    workflow.approve_order(session, stored_order.id, boss=boss, allow_partial_fulfillment=False)
    inventory = InventoryRepository()
    inventory.stock_product(session, stored_order.items[0].product, 2)
    inventory.allocate_approved_orders(session)

    shipments = ShipmentRepository()
    shipment = shipments.create_shipment(
        session, stored_order.id, {stored_order.items[0].id: 2}
    )

    for status in (
        ShipmentStatus.PICKING,
        ShipmentStatus.LABELING,
        ShipmentStatus.PACKING,
        ShipmentStatus.READY_TO_SHIP,
    ):
        shipment = shipments.advance_status(session, shipment.id, status)

    assert shipment.status == ShipmentStatus.READY_TO_SHIP.value
    assert stored_order.status == OrderStatus.READY_TO_SHIP.value


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="Set RUN_POSTGRES_TESTS=1 to run against the configured local PostgreSQL database.",
)
def test_postgresql_product_round_trip() -> None:
    engine = create_database_engine()
    assert engine.dialect.name == "postgresql"
    session_factory = create_session_factory(engine)
    sku = f"PYTEST-{uuid4().hex[:12].upper()}"

    with session_factory.begin() as session:
        saved = ProductRepository().add(
            session, Product(sku, "PostgreSQL Test Product", "M", "19.99")
        )
        saved_id = saved.id

    try:
        with session_factory() as session:
            retrieved = session.get(type(saved), saved_id)
            assert retrieved is not None
            assert retrieved.sku == sku
            assert retrieved.unit_price == Decimal("19.99")
    finally:
        with session_factory.begin() as session:
            product = session.get(type(saved), saved_id)
            if product is not None:
                session.delete(product)
    engine.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="Set RUN_POSTGRES_TESTS=1 to run against the configured local PostgreSQL database.",
)
def test_postgresql_boss_approval_locks_only_the_order_row() -> None:
    """PostgreSQL accepts the approval lock even when resident data is loaded."""

    engine = create_database_engine()
    assert engine.dialect.name == "postgresql"
    session_factory = create_session_factory(engine)
    suffix = uuid4().hex[:12].upper()
    facility_id = resident_id = requester_id = boss_id = product_id = order_id = None

    try:
        with session_factory.begin() as session:
            facility = FacilityModel(
                name=f"PostgreSQL Approval Facility {suffix}",
                shipping_address="1 PostgreSQL Road",
            )
            resident = ResidentModel(facility=facility, full_name="PostgreSQL Resident")
            requester = UserModel(
                facility=facility,
                full_name="PostgreSQL Requester",
                role=UserRole.AUTHORIZED_REQUESTER.value,
            )
            boss = UserModel(full_name="PostgreSQL Boss", role=UserRole.BOSS.value)
            session.add_all([facility, resident, requester, boss])
            session.flush()

            product = Product(f"POSTGRES-APPROVAL-{suffix}", "Approval Shirt", "M", "20.00")
            saved_product = ProductRepository().add(session, product)
            order = Order("100.00")
            order.add_item(product)
            order.submit_for_approval("5.00")
            saved_order = OrderRepository().add(
                session, order, resident=resident, requester=requester
            )

            facility_id = facility.id
            resident_id = resident.id
            requester_id = requester.id
            boss_id = boss.id
            product_id = saved_product.id
            order_id = saved_order.id

        with session_factory.begin() as session:
            boss = session.get(UserModel, boss_id)
            assert boss is not None
            approved = OrderWorkflowRepository().approve_order(
                session,
                order_id,
                boss=boss,
                allow_partial_fulfillment=True,
            )

            assert approved.status == OrderStatus.APPROVED.value
            assert approved.approvals[0].decision == "approved"

    finally:
        with session_factory.begin() as session:
            if order_id is not None:
                order = session.get(OrderModel, order_id)
                if order is not None:
                    session.delete(order)
            if product_id is not None:
                product = session.get(ProductModel, product_id)
                if product is not None:
                    session.delete(product)
            if requester_id is not None:
                requester = session.get(UserModel, requester_id)
                if requester is not None:
                    session.delete(requester)
            if boss_id is not None:
                boss = session.get(UserModel, boss_id)
                if boss is not None:
                    session.delete(boss)
            if resident_id is not None:
                resident = session.get(ResidentModel, resident_id)
                if resident is not None:
                    session.delete(resident)
            if facility_id is not None:
                facility = session.get(FacilityModel, facility_id)
                if facility is not None:
                    session.delete(facility)
    engine.dispose()
