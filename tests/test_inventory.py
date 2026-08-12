from datetime import datetime, timedelta, timezone

import pytest

from inventory import Inventory
from orders import Order, Product


@pytest.fixture
def shirt() -> Product:
    return Product("SHIRT-M-BLU", "Blue Shirt", "M", "25.00")


def approved_order(
    product: Product,
    quantity: int,
    approved_at: datetime,
    *,
    allow_partial_fulfillment: bool,
) -> Order:
    order = Order("500.00")
    order.add_item(product, quantity)
    order.submit_for_approval("5.00")
    order.approve(allow_partial_fulfillment=allow_partial_fulfillment)
    order.approved_at = approved_at
    return order


def test_earlier_approved_order_has_priority_when_stock_is_limited(shirt: Product) -> None:
    inventory = Inventory()
    inventory.stock_product(shirt, 3)
    now = datetime.now(timezone.utc)
    earlier_order = approved_order(shirt, 2, now, allow_partial_fulfillment=False)
    later_order = approved_order(
        shirt, 2, now + timedelta(seconds=1), allow_partial_fulfillment=True
    )

    allocations = inventory.allocate_approved_orders([later_order, earlier_order])

    earlier, later = allocations
    assert earlier.order_id == earlier_order.order_id
    assert earlier.is_fully_reserved is True
    assert later.order_id == later_order.order_id
    assert later.line_allocations[0].reserved_quantity == 1
    assert later.is_fully_reserved is False
    assert inventory.available_quantity(shirt.sku) == 0


def test_withheld_order_reserves_its_available_stock_before_later_orders(shirt: Product) -> None:
    inventory = Inventory()
    inventory.stock_product(shirt, 2)
    now = datetime.now(timezone.utc)
    withheld_order = approved_order(shirt, 3, now, allow_partial_fulfillment=False)
    later_order = approved_order(
        shirt, 1, now + timedelta(seconds=1), allow_partial_fulfillment=True
    )

    withheld, later = inventory.allocate_approved_orders([withheld_order, later_order])

    assert withheld.line_allocations[0].reserved_quantity == 2
    assert withheld.can_prepare_for_shipment is False
    assert later.line_allocations[0].reserved_quantity == 0


def test_partial_order_can_receive_restocked_items_later(shirt: Product) -> None:
    inventory = Inventory()
    inventory.stock_product(shirt, 1)
    order = approved_order(
        shirt,
        3,
        datetime.now(timezone.utc),
        allow_partial_fulfillment=True,
    )

    first_allocation = inventory.allocate_approved_orders([order])[0]
    inventory.stock_product(shirt, 2)
    second_allocation = inventory.allocate_approved_orders([order])[0]

    assert first_allocation.is_fully_reserved is False
    assert first_allocation.can_prepare_for_shipment is True
    assert second_allocation.is_fully_reserved is True
    assert inventory.reserved_quantity(order.order_id, shirt.sku) == 3


def test_inventory_refuses_unapproved_orders(shirt: Product) -> None:
    inventory = Inventory()
    inventory.stock_product(shirt, 1)
    draft = Order("100.00")
    draft.add_item(shirt)

    with pytest.raises(ValueError, match="Only approved orders"):
        inventory.allocate_approved_orders([draft])
