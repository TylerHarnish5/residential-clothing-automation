"""Core order-domain models for the residential clothing workflow.

This module deliberately contains no database, API, or authentication code.
Those concerns can use these models as the project grows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from .pricing import (
    MoneyInput,
    calculate_item_subtotal,
    calculate_line_subtotal,
    calculate_order_total,
    money,
)

class OrderType(str, Enum):
    NORMAL = "normal"
    DONATION = "donation"
    COMPENSATORY = "compensatory"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    READY_TO_SHIP = "ready_to_ship"


@dataclass
class Product:
    """A currently catalogued, sellable product variant."""

    sku: str
    name: str
    size: str
    unit_price: MoneyInput
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.sku.strip():
            raise ValueError("Product SKU cannot be empty")
        if not self.name.strip():
            raise ValueError("Product name cannot be empty")
        if not self.size.strip():
            raise ValueError("Product size cannot be empty")
        self.unit_price = money(self.unit_price)


@dataclass(frozen=True)
class OrderItem:
    """A product selection with product and price details frozen in time."""

    product: Product = field(repr=False, compare=False)
    quantity: int = 1
    sku: str = field(init=False)
    product_name: str = field(init=False)
    size: str = field(init=False)
    unit_price: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise TypeError("Quantity must be an integer")
        if self.quantity <= 0:
            raise ValueError("Quantity must be greater than zero")

        object.__setattr__(self, "sku", self.product.sku)
        object.__setattr__(self, "product_name", self.product.name)
        object.__setattr__(self, "size", self.product.size)
        object.__setattr__(self, "unit_price", self.product.unit_price)

    @property
    def subtotal(self) -> Decimal:
        return calculate_line_subtotal(self.unit_price, self.quantity)


@dataclass(frozen=True)
class Invoice:
    """The single invoice issued for an order, including its fixed shipping."""

    order_id: UUID
    item_subtotal: Decimal
    shipping_cost: Decimal
    total: Decimal


@dataclass
class Order:
    """A per-resident order with a separate, non-transferable budget."""

    budget_amount: MoneyInput
    order_type: OrderType = OrderType.NORMAL
    order_id: UUID = field(default_factory=uuid4)
    items: list[OrderItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.DRAFT
    shipping_cost: Decimal | None = None
    partial_fulfillment_approved: bool | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        self.budget_amount = money(self.budget_amount)
        self.order_type = OrderType(self.order_type)

    @property
    def item_subtotal(self) -> Decimal:
        return calculate_item_subtotal(item.subtotal for item in self.items)

    @property
    def total(self) -> Decimal:
        shipping = self.shipping_cost if self.shipping_cost is not None else Decimal("0")
        return calculate_order_total(self.item_subtotal, shipping)

    def add_item(self, product: Product, quantity: int = 1) -> OrderItem:
        """Add an item to a draft, capturing the product's current price."""

        self._require_draft()
        item = OrderItem(product=product, quantity=quantity)
        if self.item_subtotal + item.subtotal > self.budget_amount:
            raise ValueError("Order items would exceed the order budget")

        self.items.append(item)
        return item

    def remove_item(self, item: OrderItem) -> None:
        """Remove an item from a draft order."""

        self._require_draft()
        self.items.remove(item)

    def submit_for_approval(self, shipping_cost: MoneyInput) -> Invoice:
        """Set the one shipping charge and validate the order for Boss approval."""

        self._require_draft()
        proposed_shipping_cost = money(shipping_cost)
        validate_order_for_submission(self, proposed_shipping_cost)
        self.shipping_cost = proposed_shipping_cost
        self.status = OrderStatus.PENDING_APPROVAL
        return self.invoice()

    def approve(self, *, allow_partial_fulfillment: bool) -> None:
        """Record the Boss's order and partial-fulfillment decision."""

        approve_order(self, allow_partial_fulfillment=allow_partial_fulfillment)

    def reject(self) -> None:
        """Record the Boss's rejection of an order awaiting approval."""

        reject_order(self)

    def invoice(self) -> Invoice:
        """Return the single order invoice once its shipping cost is set."""

        if self.shipping_cost is None:
            raise ValueError("Shipping cost must be set before creating an invoice")
        return Invoice(
            order_id=self.order_id,
            item_subtotal=self.item_subtotal,
            shipping_cost=self.shipping_cost,
            total=self.total,
        )

    def _require_draft(self) -> None:
        if self.status is not OrderStatus.DRAFT:
            raise ValueError("Only draft orders can be edited")



def validate_order_for_submission(order: Order, shipping_cost: MoneyInput) -> None:
    """Validate the business rules that apply before Boss approval."""

    unavailable_items = [item.sku for item in order.items if not item.product.is_active]
    if unavailable_items:
        unavailable = ", ".join(unavailable_items)
        raise ValueError(f"Order contains unavailable product(s): {unavailable}")

    proposed_total = calculate_order_total(order.item_subtotal, shipping_cost)
    if proposed_total > order.budget_amount:
        raise ValueError("Order total, including shipping, exceeds the order budget")
    if order.order_type is OrderType.NORMAL and not order.items:
        raise ValueError("Normal orders must contain at least one item")
    if order.order_type is OrderType.NORMAL and proposed_total == Decimal("0.00"):
        raise ValueError("Normal orders must have a total greater than zero")


def approve_order(order: Order, *, allow_partial_fulfillment: bool) -> None:
    """Apply the Boss's approval decision to an order awaiting approval."""

    if order.status is not OrderStatus.PENDING_APPROVAL:
        raise ValueError("Only orders pending approval can be approved")
    if not isinstance(allow_partial_fulfillment, bool):
        raise TypeError("Partial fulfillment approval must be a boolean")

    order.partial_fulfillment_approved = allow_partial_fulfillment
    order.approved_at = datetime.now(timezone.utc)
    order.status = OrderStatus.APPROVED


def reject_order(order: Order) -> None:
    """Apply the Boss's rejection decision to an order awaiting approval."""

    if order.status is not OrderStatus.PENDING_APPROVAL:
        raise ValueError("Only orders pending approval can be rejected")
    order.status = OrderStatus.REJECTED
