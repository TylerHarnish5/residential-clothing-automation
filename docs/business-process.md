# Business Process

The system models a small clothing provider that fulfills clothing
orders for residents of nursing homes and other residential facilities.

## Actors

- **Resident:** The person receiving the clothing. A resident belongs to
  one facility.
- **Facility:** A residential home. A facility can have many residents.
- **Authorized requester:** A facility staff member who creates orders
  and can request changes or cancellations.
- **Boss:** The company decision-maker who approves, rejects, and may
  manually edit orders and requested changes.
- **Warehouse worker:** Picks, labels, and packs approved clothing.
- **Internal administrator:** An internal user who supports the process.

## Order initiation

Each order receives its own authorized budget for one resident. The
budget applies only to that order; any unused amount cannot be used for
a later order.

Examples:

- $500
- $1,000
- $1,500

An authorized requester creates a draft order by selecting clothing
from the catalog. A product's unit price is locked when it is added to
the draft. If the draft is abandoned and redone, the new draft uses the
current catalog prices.

A draft has one line per product SKU. Selecting the same SKU again
increases that line's quantity and keeps the original locked unit price.

The order total includes clothing and a single shipping charge. Shipping
is set when the order is submitted. The resident receives one invoice
per order, so later partial shipments do not add another shipping charge.

An order does not need to use its full budget, but it cannot exceed it.

Invariant for a normal order:

0 < order_total <= budget_amount

Donation or compensatory orders are the exception and may have a zero
total. They must be identified as a distinct order type.

## Approval and changes

All orders require the Boss's approval before warehouse fulfillment.
The Boss may approve, reject, or manually edit the request.

After an order is approved, an authorized requester may request a
change or cancellation. The request is not guaranteed; it requires the
Boss's approval. The Boss makes the final decision for all order changes
and cancellations.

When a draft is submitted for approval, unavailable, deactivated, or
discontinued catalog items prevent approval. The requester is notified
that the item is no longer available. Locked draft prices do not change
at this point.

## Inventory and fulfillment

Approved orders are made available to warehouse employees. When stock
is limited, earlier approved orders are prioritized over later approved
orders.

### V0 workflow automation

The Boss still approves every order. Immediately after a Boss approval,
the system automatically reserves available inventory in approval-priority
order and creates a pending fulfillment task for stock that can be prepared.
Receiving additional inventory reruns this same allocation and may create a
later fulfillment task for newly available items.

A fulfillment task is a specific group of reserved order items for warehouse
staff to pick, label, and pack. It follows the V0 warehouse sequence:
`pending` → `picking` → `labeling` → `packing` → `ready to ship`.

The Boss decides whether an order with unavailable items should be
partially fulfilled or withheld until the remaining items are available.
When partial fulfillment is approved, a task may be created for available
items and the remaining items receive a later task after restocking. If
partial fulfillment is not approved, the system reserves available stock for
the earlier order but creates no task until all its items are available. No
additional shipping charge is added to the order.

If the Boss approves an order cancellation, the system stops every fulfillment
task for that order by marking it `cancelled` and releases its inventory
reservations. A cancelled task cannot advance through the warehouse workflow.
This cancellation decision overrides the normal `ready to ship` terminal state
because V0 does not send packages after that state.

Warehouse workers:

1. Pick the specified clothing.
2. Label each garment with the resident's name.
3. Pack the clothing.
4. Prepare the package for shipment to the resident's facility.

For V0, `ready to ship` is the terminal fulfillment state.
