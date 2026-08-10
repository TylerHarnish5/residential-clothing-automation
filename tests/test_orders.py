from decimal import Decimal

import pytest

from orders import Order, OrderStatus, OrderType, Product, money


@pytest.fixture
def shirt() -> Product:
    return Product("SHIRT-M-BLU", "Blue Shirt", "M", "25.00")


def test_money_uses_two_decimal_places_and_rejects_float_values() -> None:
    assert money("10.005") == Decimal("10.01")

    with pytest.raises(TypeError, match="not a float"):
        money(10.00)


def test_order_item_snapshots_catalog_details_and_price(shirt: Product) -> None:
    order = Order("100.00")
    item = order.add_item(shirt, quantity=2)

    shirt.name = "Updated Blue Shirt"
    shirt.size = "L"
    shirt.unit_price = Decimal("30.00")

    assert item.sku == "SHIRT-M-BLU"
    assert item.product_name == "Blue Shirt"
    assert item.size == "M"
    assert item.unit_price == Decimal("25.00")
    assert item.subtotal == Decimal("50.00")


def test_invoice_total_includes_the_one_fixed_shipping_charge(shirt: Product) -> None:
    order = Order("100.00")
    order.add_item(shirt, quantity=2)

    invoice = order.submit_for_approval("10.00")

    assert invoice.item_subtotal == Decimal("50.00")
    assert invoice.shipping_cost == Decimal("10.00")
    assert invoice.total == Decimal("60.00")
    assert order.total == Decimal("60.00")

    with pytest.raises(ValueError, match="Only draft orders"):
        order.submit_for_approval("15.00")

    assert order.shipping_cost == Decimal("10.00")


def test_shipping_cannot_push_an_order_over_its_budget(shirt: Product) -> None:
    order = Order("55.00")
    order.add_item(shirt, quantity=2)

    with pytest.raises(ValueError, match="including shipping"):
        order.submit_for_approval("10.00")

    assert order.status is OrderStatus.DRAFT
    assert order.shipping_cost is None


def test_deactivated_product_blocks_a_draft_at_submission(shirt: Product) -> None:
    order = Order("100.00")
    order.add_item(shirt)
    shirt.is_active = False

    with pytest.raises(ValueError, match="SHIRT-M-BLU"):
        order.submit_for_approval("5.00")

    assert order.status is OrderStatus.DRAFT
    assert order.shipping_cost is None


def test_normal_orders_require_an_item_and_a_positive_total() -> None:
    with pytest.raises(ValueError, match="at least one item"):
        Order("20.00").submit_for_approval("5.00")

    free_product = Product("FREE-SOCKS", "Free Socks", "One Size", "0")
    free_order = Order("0")
    free_order.add_item(free_product)

    with pytest.raises(ValueError, match="greater than zero"):
        free_order.submit_for_approval("0")


def test_donation_order_may_have_a_zero_total() -> None:
    donation = Order("0", order_type=OrderType.DONATION)

    invoice = donation.submit_for_approval("0")

    assert invoice.total == Decimal("0.00")
    assert donation.status is OrderStatus.PENDING_APPROVAL


def test_boss_approval_records_partial_fulfillment_decision(shirt: Product) -> None:
    order = Order("100.00")
    order.add_item(shirt)
    order.submit_for_approval("5.00")

    order.approve(allow_partial_fulfillment=True)

    assert order.status is OrderStatus.APPROVED
    assert order.partial_fulfillment_approved is True

    with pytest.raises(ValueError, match="Only draft orders"):
        order.add_item(shirt)


def test_orders_can_only_be_approved_or_rejected_while_pending() -> None:
    order = Order("100.00")

    with pytest.raises(ValueError, match="pending approval"):
        order.approve(allow_partial_fulfillment=False)

    with pytest.raises(ValueError, match="pending approval"):
        order.reject()
