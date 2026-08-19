"""Synchronous V0 automation for approved orders and warehouse work."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .db_models import ShipmentModel
from .reliability import logger
from .repositories import InventoryRepository, ShipmentRepository


class FulfillmentAutomation:
    """Reserve stock and create pending warehouse work after a workflow event.

    This is deliberately invoked by the approval and stock-receipt API actions.
    V0 does not need a background worker to perform this deterministic work.
    """

    def process(self, session: Session) -> list[ShipmentModel]:
        """Allocate approved orders by priority and create eligible shipments.

        A shipment is the project's persistent fulfillment task: it identifies
        the reserved items that warehouse staff must pick, label, and pack.
        """

        allocations = InventoryRepository().allocate_approved_orders(session)
        shipments = ShipmentRepository()
        created: list[ShipmentModel] = []

        for allocation in allocations:
            shipment = shipments.create_task_for_allocation(session, allocation)
            if shipment is not None:
                created.append(shipment)

        logger.info(
            "fulfillment_automation_processed approved_orders=%s created_tasks=%s",
            len(allocations),
            len(created),
        )
        return created
