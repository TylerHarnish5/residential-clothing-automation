# Business Process

The system models a small clothing provider that fulfills clothing
orders for residents of nursing homes and other residential facilities.

## Order initiation

An authorized budget is established for a resident, usually by the
facility or the resident's family.

Examples:

- $500
- $1,000
- $1,500

Clothing is then selected from the company's catalog up to, but never
above, the allocated budget.

An order does not need to equal the budget exactly.

Invariant:

0 <= order_total <= budget_amount

## Fulfillment

Once clothing selection is finalized, the order is confirmed and made
available to warehouse employees.

Warehouse workers:

1. Pick the specified clothing.
2. Label each garment with the resident's name.
3. Pack the clothing.
4. Prepare the package for shipment to the facility.