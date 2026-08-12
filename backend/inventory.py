"""In-memory inventory allocation for approved clothing orders.

Inventory reservations are deliberately separate from warehouse picking. A
reservation promises available stock to an approved order; a later warehouse
feature can turn those reserved items into packages and shipments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from uuid import UUID

from .orders import Order, OrderStatus, Product


def _require_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise TypeError("Inventory quantity must be an integer")
    if quantity < 0:
        raise ValueError("Inventory quantity cannot be negative")


@dataclass
class InventoryItem:
    """Stock counts for one product SKU."""

    product: Product
    quantity_on_hand: int = 0
    reserved_quantity: int = 0

    def __post_init__(self) -> None:
        _require_quantity(self.quantity_on_hand)
        _require_quantity(self.reserved_quantity)
        if self.reserved_quantity > self.quantity_on_hand:
            raise ValueError("Reserved quantity cannot exceed stock on hand")

    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.reserved_quantity


@dataclass(frozen=True)
class LineAllocation:
    """The stock currently reserved for one SKU on an order."""

    sku: str
    requested_quantity: int
    reserved_quantity: int

    @property
    def is_fully_reserved(self) -> bool:
        return self.reserved_quantity == self.requested_quantity


@dataclass(frozen=True)
class OrderAllocation:
    """The current inventory allocation for one approved order."""

    order_id: UUID
    line_allocations: tuple[LineAllocation, ...]
    partial_fulfillment_permitted: bool

    @property
    def is_fully_reserved(self) -> bool:
        return all(line.is_fully_reserved for line in self.line_allocations)

    @property
    def can_prepare_for_shipment(self) -> bool:
        """True when the whole order is ready or the Boss allowed a partial ship."""

        return self.is_fully_reserved or self.partial_fulfillment_permitted


@dataclass
class Inventory:
    """V0 in-memory stock records and reservations by approved order."""

    items: dict[str, InventoryItem] = field(default_factory=dict)
    _reservations: dict[UUID, dict[str, int]] = field(default_factory=dict, init=False)

    def stock_product(self, product: Product, quantity: int) -> InventoryItem:
        """Add stock for a product SKU, creating its inventory record if needed."""

        _require_quantity(quantity)
        record = self.items.get(product.sku)
        if record is None:
            record = InventoryItem(product=product, quantity_on_hand=quantity)
            self.items[product.sku] = record
        else:
            record.quantity_on_hand += quantity
        return record

    def available_quantity(self, sku: str) -> int:
        """Return unreserved stock for a SKU, or zero if it is not stocked."""

        record = self.items.get(sku)
        return record.available_quantity if record is not None else 0

    def reserved_quantity(self, order_id: UUID, sku: str) -> int:
        """Return how many units of a SKU are reserved for one order."""

        return self._reservations.get(order_id, {}).get(sku, 0)

    def allocate_approved_orders(self, orders: Iterable[Order]) -> list[OrderAllocation]:
        """Reserve inventory for approved orders in Boss-approval order.

        Available units are reserved for older approved orders before newer
        ones. When stock is insufficient, the reservation remains incomplete.
        The Boss's partial-fulfillment decision controls whether warehouse work
        may start with that incomplete allocation; it does not allow newer
        orders to take stock promised to an earlier order.
        """

        approved_orders = list(orders)
        for order in approved_orders:
            if order.status is not OrderStatus.APPROVED:
                raise ValueError("Only approved orders can receive inventory")
            if order.approved_at is None:
                raise ValueError("Approved orders must have an approval timestamp")

        allocations: list[OrderAllocation] = []
        for order in sorted(approved_orders, key=lambda order: order.approved_at):
            requested = self._requested_quantities(order)
            order_reservations = self._reservations.setdefault(order.order_id, {})

            for sku, requested_quantity in requested.items():
                already_reserved = order_reservations.get(sku, 0)
                remaining_quantity = requested_quantity - already_reserved
                if remaining_quantity <= 0:
                    continue

                reservable_quantity = min(remaining_quantity, self.available_quantity(sku))
                if reservable_quantity:
                    self._reserve(order.order_id, sku, reservable_quantity)

            allocations.append(self._allocation_for(order, requested))

        return allocations

    @staticmethod
    def _requested_quantities(order: Order) -> dict[str, int]:
        requested: dict[str, int] = {}
        for item in order.items:
            requested[item.sku] = requested.get(item.sku, 0) + item.quantity
        return requested

    def _reserve(self, order_id: UUID, sku: str, quantity: int) -> None:
        record = self.items[sku]
        record.reserved_quantity += quantity
        order_reservations = self._reservations.setdefault(order_id, {})
        order_reservations[sku] = order_reservations.get(sku, 0) + quantity

    def _allocation_for(
        self, order: Order, requested_quantities: dict[str, int]
    ) -> OrderAllocation:
        lines = tuple(
            LineAllocation(
                sku=sku,
                requested_quantity=quantity,
                reserved_quantity=self.reserved_quantity(order.order_id, sku),
            )
            for sku, quantity in requested_quantities.items()
        )
        return OrderAllocation(
            order_id=order.order_id,
            line_allocations=lines,
            partial_fulfillment_permitted=bool(order.partial_fulfillment_approved),
        )
