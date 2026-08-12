"""Small, reusable pricing functions for orders and invoices."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Union


MoneyInput = Union[Decimal, int, str]
CENT = Decimal("0.01")


def money(value: MoneyInput) -> Decimal:
    """Convert a money value to a non-negative, two-decimal Decimal."""

    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("Money must be an int, str, or Decimal; not a float")

    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Invalid money value: {value!r}") from error

    if not amount.is_finite() or amount < 0:
        raise ValueError("Money must be a non-negative finite amount")

    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_line_subtotal(unit_price: MoneyInput, quantity: int) -> Decimal:
    """Calculate the total for a positive whole-number quantity of one item."""

    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise TypeError("Quantity must be an integer")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero")

    return money(money(unit_price) * quantity)


def calculate_item_subtotal(line_subtotals: Iterable[MoneyInput]) -> Decimal:
    """Calculate the clothing portion of an invoice from line subtotals."""

    return money(sum((money(subtotal) for subtotal in line_subtotals), Decimal("0")))


def calculate_order_total(item_subtotal: MoneyInput, shipping_cost: MoneyInput) -> Decimal:
    """Calculate an order total, including its one fixed shipping charge."""

    return money(money(item_subtotal) + money(shipping_cost))
