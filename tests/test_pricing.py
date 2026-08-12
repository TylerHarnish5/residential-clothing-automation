from decimal import Decimal

import pytest

from pricing import calculate_item_subtotal, calculate_line_subtotal, calculate_order_total


def test_pricing_calculates_line_item_and_order_totals() -> None:
    assert calculate_line_subtotal("12.50", 2) == Decimal("25.00")
    assert calculate_item_subtotal(["25.00", "10.25"]) == Decimal("35.25")
    assert calculate_order_total("35.25", "7.50") == Decimal("42.75")


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_line_pricing_rejects_invalid_quantities(quantity: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        calculate_line_subtotal("10.00", quantity)  # type: ignore[arg-type]
