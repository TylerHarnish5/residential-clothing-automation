"""add_cancelled_shipment_status

Revision ID: b91c7d8e4f52
Revises: 7313b592b2e1
Create Date: 2026-08-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b91c7d8e4f52"
down_revision: Union[str, Sequence[str], None] = "7313b592b2e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shipments") as batch_op:
        batch_op.drop_constraint("ck_shipments_status", type_="check")
        batch_op.create_check_constraint(
            "ck_shipments_status",
            "status IN ('pending', 'picking', 'labeling', 'packing', 'ready_to_ship', 'cancelled')",
        )


def downgrade() -> None:
    with op.batch_alter_table("shipments") as batch_op:
        batch_op.drop_constraint("ck_shipments_status", type_="check")
        batch_op.create_check_constraint(
            "ck_shipments_status",
            "status IN ('pending', 'picking', 'labeling', 'packing', 'ready_to_ship')",
        )
