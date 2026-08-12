"""Persistence operations that translate between domain objects and ORM models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from .db_models import (
    ApprovalDecision,
    ApprovalModel,
    ChangeRequestModel,
    ChangeRequestStatus,
    ChangeRequestType,
    InventoryItemModel,
    InventoryReservationModel,
    InvoiceModel,
    OrderItemModel,
    OrderModel,
    ProductModel,
    ResidentModel,
    ShipmentItemModel,
    ShipmentModel,
    ShipmentStatus,
    UserModel,
    UserRole,
)
from .inventory import LineAllocation, OrderAllocation
from .orders import Order, OrderItem, OrderStatus, Product, validate_order_for_submission


class ProductRepository:
    """Save and retrieve catalog products."""

    def add(self, session: Session, product: Product) -> ProductModel:
        if session.scalar(select(ProductModel).where(ProductModel.sku == product.sku)):
            raise ValueError(f"Product SKU already exists: {product.sku}")

        model = ProductModel(
            sku=product.sku,
            name=product.name,
            size=product.size,
            unit_price=product.unit_price,
            is_active=product.is_active,
        )
        session.add(model)
        session.flush()
        return model

    def get_domain_product(self, session: Session, sku: str) -> Product:
        model = session.scalar(select(ProductModel).where(ProductModel.sku == sku))
        if model is None:
            raise ValueError(f"Unknown product SKU: {sku}")
        return Product(model.sku, model.name, model.size, model.unit_price, model.is_active)


class OrderRepository:
    """Persist and reconstruct validated domain orders."""

    def add(
        self,
        session: Session,
        order: Order,
        *,
        resident: ResidentModel,
        requester: UserModel | None = None,
        boss: UserModel | None = None,
    ) -> OrderModel:
        if session.get(OrderModel, order.order_id) is not None:
            raise ValueError(f"Order already exists: {order.order_id}")
        if order.status is OrderStatus.APPROVED and boss is None:
            raise ValueError("An approved order requires the Boss who approved it")
        self._validate_for_persistence(order)
        self._validate_actors(resident=resident, requester=requester, boss=boss)

        model = OrderModel(
            id=order.order_id,
            resident=resident,
            requester=requester,
            order_type=order.order_type.value,
            status=order.status.value,
            budget_amount=order.budget_amount,
            resident_name_snapshot=resident.full_name,
            shipping_address_snapshot=resident.facility.shipping_address,
            partial_fulfillment_approved=order.partial_fulfillment_approved,
            approved_at=order.approved_at,
        )
        with session.no_autoflush:
            for position, item in enumerate(order.items, start=1):
                product = session.scalar(
                    select(ProductModel).where(ProductModel.sku == item.sku)
                )
                if product is None:
                    raise ValueError(f"Cannot save order with unknown product SKU: {item.sku}")
                model.items.append(self._order_item_model(item, product, position))

        if order.shipping_cost is not None:
            invoice = order.invoice()
            model.invoice = InvoiceModel(
                item_subtotal=invoice.item_subtotal,
                shipping_cost=invoice.shipping_cost,
                total=invoice.total,
            )

        if order.status is OrderStatus.APPROVED:
            model.approvals.append(
                ApprovalModel(
                    boss=boss,
                    decision=ApprovalDecision.APPROVED.value,
                    decided_at=order.approved_at,
                )
            )

        session.add(model)
        session.flush()
        return model

    def get_domain_order(self, session: Session, order_id: UUID) -> Order:
        statement = (
            select(OrderModel)
            .where(OrderModel.id == order_id)
            .options(joinedload(OrderModel.items).joinedload(OrderItemModel.product))
        )
        model = session.execute(statement).unique().scalar_one_or_none()
        if model is None:
            raise ValueError(f"Unknown order: {order_id}")

        return self._to_domain_order(model)

    @staticmethod
    def _to_domain_order(model: OrderModel) -> Order:
        """Rebuild a domain order from its persisted price and product snapshots."""

        domain_order = Order(
            budget_amount=model.budget_amount,
            order_type=model.order_type,
            order_id=model.id,
        )
        domain_order.items = [OrderRepository._domain_item(item) for item in model.items]
        domain_order.status = OrderStatus(model.status)
        domain_order.shipping_cost = model.invoice.shipping_cost if model.invoice else None
        domain_order.partial_fulfillment_approved = model.partial_fulfillment_approved
        domain_order.approved_at = model.approved_at
        return domain_order

    @staticmethod
    def _order_item_model(
        item: OrderItem, product: ProductModel, position: int
    ) -> OrderItemModel:
        return OrderItemModel(
            product=product,
            position=position,
            sku_snapshot=item.sku,
            product_name_snapshot=item.product_name,
            size_snapshot=item.size,
            unit_price=item.unit_price,
            quantity=item.quantity,
        )

    @staticmethod
    def _domain_item(item: OrderItemModel) -> OrderItem:
        snapshot_product = Product(
            sku=item.sku_snapshot,
            name=item.product_name_snapshot,
            size=item.size_snapshot,
            unit_price=item.unit_price,
            is_active=item.product.is_active if item.product else False,
        )
        return OrderItem(snapshot_product, item.quantity)

    @staticmethod
    def _validate_for_persistence(order: Order) -> None:
        """Keep persisted invoice data inside the domain layer's budget rules."""

        if order.status in {OrderStatus.PENDING_APPROVAL, OrderStatus.APPROVED}:
            if order.shipping_cost is None:
                raise ValueError("Submitted and approved orders require an invoice")
            if order.total > order.budget_amount:
                raise ValueError("Order total, including shipping, exceeds the order budget")
            if order.order_type.value == "normal" and order.total == Decimal("0.00"):
                raise ValueError("Normal orders must have a total greater than zero")

    @staticmethod
    def _validate_actors(
        *,
        resident: ResidentModel,
        requester: UserModel | None,
        boss: UserModel | None,
    ) -> None:
        if requester is not None:
            if requester.role != UserRole.AUTHORIZED_REQUESTER.value:
                raise ValueError("Order requester must have the authorized requester role")
            if requester.facility_id != resident.facility_id:
                raise ValueError("Order requester must belong to the resident's facility")
        if boss is not None and boss.role != UserRole.BOSS.value:
            raise ValueError("Order approver must have the Boss role")


class InventoryRepository:
    """Persist stock and one cumulative reservation per order item."""

    def stock_product(self, session: Session, product: ProductModel, quantity: int) -> InventoryItemModel:
        self._require_positive_quantity(quantity)
        inventory = product.inventory_item
        if inventory is None:
            inventory = InventoryItemModel(product=product, quantity_on_hand=quantity)
            session.add(inventory)
        else:
            inventory.quantity_on_hand += quantity
        session.flush()
        return inventory

    def reserve(self, session: Session, order_item: OrderItemModel, quantity: int) -> InventoryReservationModel:
        """Reserve available stock without exceeding the requested line quantity."""

        self._require_positive_quantity(quantity)
        if order_item.order.status != OrderStatus.APPROVED.value:
            raise ValueError("Inventory can only be reserved for approved orders")
        if order_item.product is None or order_item.product.inventory_item is None:
            raise ValueError("Product has no inventory record")

        inventory = session.scalar(
            select(InventoryItemModel)
            .where(InventoryItemModel.id == order_item.product.inventory_item.id)
            .with_for_update()
        )
        reservation = order_item.inventory_reservation
        already_reserved = reservation.quantity if reservation else 0
        if already_reserved + quantity > order_item.quantity:
            raise ValueError("Reservation would exceed the order item quantity")
        if inventory.quantity_on_hand - inventory.reserved_quantity < quantity:
            raise ValueError("Reservation would exceed available inventory")

        if reservation is None:
            reservation = InventoryReservationModel(order_item=order_item, quantity=quantity)
            session.add(reservation)
        else:
            reservation.quantity += quantity
        inventory.reserved_quantity += quantity
        session.flush()
        return reservation

    def allocate_approved_orders(self, session: Session) -> list[OrderAllocation]:
        """Reserve stock for every approved order in Boss-approval order."""

        statement = (
            select(OrderModel)
            .where(OrderModel.status == OrderStatus.APPROVED.value)
            .order_by(OrderModel.approved_at, OrderModel.id)
            .options(
                selectinload(OrderModel.items).selectinload(OrderItemModel.product).selectinload(
                    ProductModel.inventory_item
                ),
                selectinload(OrderModel.items).selectinload(OrderItemModel.inventory_reservation),
            )
        )
        orders = session.scalars(statement).all()
        allocations: list[OrderAllocation] = []

        for order in orders:
            if order.approved_at is None:
                raise ValueError("Approved orders must have an approval timestamp")

            lines: list[LineAllocation] = []
            for item in sorted(order.items, key=lambda item: item.position):
                reservation = item.inventory_reservation
                already_reserved = reservation.quantity if reservation else 0
                remaining = item.quantity - already_reserved
                inventory = item.product.inventory_item if item.product else None

                if remaining > 0 and inventory is not None:
                    inventory = session.scalar(
                        select(InventoryItemModel)
                        .where(InventoryItemModel.id == inventory.id)
                        .with_for_update()
                    )
                    quantity = min(remaining, inventory.quantity_on_hand - inventory.reserved_quantity)
                    if quantity > 0:
                        if reservation is None:
                            reservation = InventoryReservationModel(order_item=item, quantity=quantity)
                            session.add(reservation)
                        else:
                            reservation.quantity += quantity
                        inventory.reserved_quantity += quantity
                        already_reserved += quantity

                lines.append(
                    LineAllocation(
                        sku=item.sku_snapshot,
                        requested_quantity=item.quantity,
                        reserved_quantity=already_reserved,
                    )
                )

            allocations.append(
                OrderAllocation(
                    order_id=order.id,
                    line_allocations=tuple(lines),
                    partial_fulfillment_permitted=bool(order.partial_fulfillment_approved),
                )
            )

        session.flush()
        return allocations

    def release_order_reservations(self, session: Session, order: OrderModel) -> None:
        """Release an order's reserved stock after an approved cancellation."""

        for item in order.items:
            reservation = item.inventory_reservation
            inventory = item.product.inventory_item if item.product else None
            if reservation is None or inventory is None:
                continue

            locked_inventory = session.scalar(
                select(InventoryItemModel)
                .where(InventoryItemModel.id == inventory.id)
                .with_for_update()
            )
            locked_inventory.reserved_quantity -= reservation.quantity
            session.delete(reservation)
        session.flush()

    @staticmethod
    def _require_positive_quantity(quantity: int) -> None:
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise TypeError("Inventory quantity must be an integer")
        if quantity <= 0:
            raise ValueError("Inventory quantity must be greater than zero")


class OrderWorkflowRepository:
    """Persist Boss approval, rejection, and post-approval requester actions."""

    def approve_order(
        self,
        session: Session,
        order_id: UUID,
        *,
        boss: UserModel,
        allow_partial_fulfillment: bool,
    ) -> OrderModel:
        if boss.role != UserRole.BOSS.value:
            raise ValueError("Order approver must have the Boss role")
        if not isinstance(allow_partial_fulfillment, bool):
            raise TypeError("Partial fulfillment approval must be a boolean")

        order = self._get_order(session, order_id)
        if order.status != OrderStatus.PENDING_APPROVAL.value:
            raise ValueError("Only orders pending approval can be approved")

        domain_order = OrderRepository._to_domain_order(order)
        if domain_order.shipping_cost is None:
            raise ValueError("Submitted orders require an invoice")
        validate_order_for_submission(domain_order, domain_order.shipping_cost)

        order.partial_fulfillment_approved = allow_partial_fulfillment
        order.approved_at = datetime.now(timezone.utc)
        order.status = OrderStatus.APPROVED.value
        order.approvals.append(
            ApprovalModel(
                boss=boss,
                decision=ApprovalDecision.APPROVED.value,
                decided_at=order.approved_at,
            )
        )
        session.flush()
        return order

    def reject_order(
        self, session: Session, order_id: UUID, *, boss: UserModel, reason: str | None = None
    ) -> OrderModel:
        if boss.role != UserRole.BOSS.value:
            raise ValueError("Order approver must have the Boss role")

        order = self._get_order(session, order_id)
        if order.status != OrderStatus.PENDING_APPROVAL.value:
            raise ValueError("Only orders pending approval can be rejected")

        order.status = OrderStatus.REJECTED.value
        order.approvals.append(
            ApprovalModel(
                boss=boss,
                decision=ApprovalDecision.REJECTED.value,
                decided_at=datetime.now(timezone.utc),
                reason=reason,
            )
        )
        session.flush()
        return order

    def request_change(
        self,
        session: Session,
        order_id: UUID,
        *,
        requester: UserModel,
        request_type: ChangeRequestType,
        requested_details: str | None = None,
    ) -> ChangeRequestModel:
        order = self._get_order(session, order_id)
        OrderRepository._validate_actors(
            resident=order.resident, requester=requester, boss=None
        )
        if order.status != OrderStatus.APPROVED.value:
            raise ValueError("Changes and cancellations can only be requested for approved orders")

        change_request = ChangeRequestModel(
            order=order,
            requester=requester,
            request_type=ChangeRequestType(request_type).value,
            requested_details=requested_details,
        )
        session.add(change_request)
        session.flush()
        return change_request

    def decide_change_request(
        self,
        session: Session,
        change_request_id: UUID,
        *,
        boss: UserModel,
        approved: bool,
        reason: str | None = None,
    ) -> ChangeRequestModel:
        if boss.role != UserRole.BOSS.value:
            raise ValueError("Change-request approver must have the Boss role")
        if not isinstance(approved, bool):
            raise TypeError("Change-request decision must be a boolean")

        change_request = session.get(ChangeRequestModel, change_request_id, with_for_update=True)
        if change_request is None:
            raise ValueError(f"Unknown change request: {change_request_id}")
        if change_request.status != ChangeRequestStatus.PENDING.value:
            raise ValueError("Only pending change requests can be decided")

        change_request.boss = boss
        change_request.decided_at = datetime.now(timezone.utc)
        change_request.boss_decision_reason = reason
        change_request.status = (
            ChangeRequestStatus.APPROVED.value if approved else ChangeRequestStatus.REJECTED.value
        )
        if approved and change_request.request_type == ChangeRequestType.CANCELLATION.value:
            order = self._get_order(session, change_request.order_id)
            InventoryRepository().release_order_reservations(session, order)
            order.status = OrderStatus.CANCELLED.value

        session.flush()
        return change_request

    @staticmethod
    def _get_order(session: Session, order_id: UUID) -> OrderModel:
        statement = (
            select(OrderModel)
            .where(OrderModel.id == order_id)
            .options(
                joinedload(OrderModel.resident),
                selectinload(OrderModel.items).selectinload(OrderItemModel.product),
                selectinload(OrderModel.items).selectinload(OrderItemModel.inventory_reservation),
                selectinload(OrderModel.invoice),
                selectinload(OrderModel.approvals),
            )
            .with_for_update()
        )
        order = session.execute(statement).unique().scalar_one_or_none()
        if order is None:
            raise ValueError(f"Unknown order: {order_id}")
        return order


class ShipmentRepository:
    """Persist V0 warehouse shipments while enforcing order and reservation limits."""

    _NEXT_STATUS = {
        ShipmentStatus.PENDING.value: ShipmentStatus.PICKING.value,
        ShipmentStatus.PICKING.value: ShipmentStatus.LABELING.value,
        ShipmentStatus.LABELING.value: ShipmentStatus.PACKING.value,
        ShipmentStatus.PACKING.value: ShipmentStatus.READY_TO_SHIP.value,
    }

    def create_shipment(
        self, session: Session, order_id: UUID, quantities: Mapping[UUID, int]
    ) -> ShipmentModel:
        order = self._get_order(session, order_id)
        if order.status != OrderStatus.APPROVED.value:
            raise ValueError("Shipments can only be created for approved orders")
        if not quantities:
            raise ValueError("A shipment must contain at least one order item")

        items_by_id = {item.id: item for item in order.items}
        if not set(quantities).issubset(items_by_id):
            raise ValueError("Shipment contains an item that does not belong to the order")

        already_shipped = self._shipped_quantities(order)
        if not order.partial_fulfillment_approved:
            for item in order.items:
                reserved = item.inventory_reservation.quantity if item.inventory_reservation else 0
                if reserved != item.quantity:
                    raise ValueError("Order is incomplete and partial fulfillment was not approved")
                if quantities.get(item.id, 0) != item.quantity - already_shipped.get(item.id, 0):
                    raise ValueError("A withheld order must ship all remaining items together")

        shipment = ShipmentModel(order=order)
        for order_item_id, quantity in quantities.items():
            self._require_positive_quantity(quantity)
            item = items_by_id[order_item_id]
            reserved = item.inventory_reservation.quantity if item.inventory_reservation else 0
            available_to_ship = reserved - already_shipped.get(order_item_id, 0)
            if quantity > available_to_ship:
                raise ValueError("Shipment quantity exceeds inventory reserved for this order item")
            shipment.items.append(ShipmentItemModel(order_item=item, quantity=quantity))

        session.add(shipment)
        session.flush()
        return shipment

    def advance_status(
        self, session: Session, shipment_id: UUID, next_status: ShipmentStatus
    ) -> ShipmentModel:
        shipment = session.get(ShipmentModel, shipment_id, with_for_update=True)
        if shipment is None:
            raise ValueError(f"Unknown shipment: {shipment_id}")

        expected_status = self._NEXT_STATUS.get(shipment.status)
        if expected_status != ShipmentStatus(next_status).value:
            raise ValueError("Shipment status must follow the warehouse workflow")

        shipment.status = expected_status
        if shipment.status == ShipmentStatus.READY_TO_SHIP.value:
            self._mark_order_ready_if_complete(session, shipment.order)
        session.flush()
        return shipment

    @staticmethod
    def _get_order(session: Session, order_id: UUID) -> OrderModel:
        statement = (
            select(OrderModel)
            .where(OrderModel.id == order_id)
            .options(
                selectinload(OrderModel.items).selectinload(OrderItemModel.inventory_reservation),
                selectinload(OrderModel.shipments).selectinload(ShipmentModel.items),
            )
            .with_for_update()
        )
        order = session.execute(statement).unique().scalar_one_or_none()
        if order is None:
            raise ValueError(f"Unknown order: {order_id}")
        return order

    @staticmethod
    def _shipped_quantities(order: OrderModel) -> dict[UUID, int]:
        quantities: dict[UUID, int] = {}
        for shipment in order.shipments:
            for item in shipment.items:
                quantities[item.order_item_id] = quantities.get(item.order_item_id, 0) + item.quantity
        return quantities

    def _mark_order_ready_if_complete(self, session: Session, order: OrderModel) -> None:
        ready_quantities = {item.id: 0 for item in order.items}
        for shipment in order.shipments:
            if shipment.status != ShipmentStatus.READY_TO_SHIP.value:
                continue
            for item in shipment.items:
                ready_quantities[item.order_item_id] += item.quantity

        if all(ready_quantities[item.id] == item.quantity for item in order.items):
            order.status = OrderStatus.READY_TO_SHIP.value

    @staticmethod
    def _require_positive_quantity(quantity: int) -> None:
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise TypeError("Shipment quantity must be an integer")
        if quantity <= 0:
            raise ValueError("Shipment quantity must be greater than zero")
