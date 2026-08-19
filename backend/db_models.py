"""SQLAlchemy mappings for the residential clothing order workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .orders import OrderStatus, OrderType


class Base(DeclarativeBase):
    """Base class for all database tables."""


class UserRole(str, Enum):
    AUTHORIZED_REQUESTER = "authorized_requester"
    BOSS = "boss"
    WAREHOUSE_WORKER = "warehouse_worker"
    INTERNAL_ADMINISTRATOR = "internal_administrator"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ChangeRequestType(str, Enum):
    CHANGE = "change"
    CANCELLATION = "cancellation"


class ChangeRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ShipmentStatus(str, Enum):
    PENDING = "pending"
    PICKING = "picking"
    LABELING = "labeling"
    PACKING = "packing"
    READY_TO_SHIP = "ready_to_ship"
    CANCELLED = "cancelled"


class IdempotencyStatus(str, Enum):
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FacilityModel(Base):
    __tablename__ = "facilities"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    shipping_address: Mapped[str] = mapped_column(Text, nullable=False)

    residents: Mapped[list[ResidentModel]] = relationship(back_populates="facility")
    users: Mapped[list[UserModel]] = relationship(back_populates="facility")


class IdempotencyRecordModel(Base):
    """Durable result or failure state for one client-supplied POST key."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        Index("ix_idempotency_records_status_created_at", "status", "created_at"),
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_idempotency_records_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    response_status: Mapped[Optional[int]] = mapped_column(Integer)
    response_body: Mapped[Optional[str]] = mapped_column(Text)
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    failure_detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ResidentModel(Base):
    __tablename__ = "residents"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    facility_id: Mapped[UUID] = mapped_column(
        ForeignKey("facilities.id"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    facility: Mapped[FacilityModel] = relationship(back_populates="residents")
    orders: Mapped[list[OrderModel]] = relationship(back_populates="resident")


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('authorized_requester', 'boss', 'warehouse_worker', "
            "'internal_administrator')",
            name="ck_users_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    facility_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("facilities.id"))
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)

    facility: Mapped[Optional[FacilityModel]] = relationship(back_populates="users")
    requested_orders: Mapped[list[OrderModel]] = relationship(
        back_populates="requester", foreign_keys="OrderModel.requester_id"
    )
    approvals: Mapped[list[ApprovalModel]] = relationship(back_populates="boss")
    requested_change_requests: Mapped[list[ChangeRequestModel]] = relationship(
        back_populates="requester", foreign_keys="ChangeRequestModel.requester_id"
    )
    decided_change_requests: Mapped[list[ChangeRequestModel]] = relationship(
        back_populates="boss", foreign_keys="ChangeRequestModel.boss_id"
    )


class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("unit_price >= 0", name="ck_products_unit_price"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    inventory_item: Mapped[Optional[InventoryItemModel]] = relationship(
        back_populates="product", uselist=False, cascade="all, delete-orphan"
    )
    order_items: Mapped[list[OrderItemModel]] = relationship(back_populates="product")


class InventoryItemModel(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="ck_inventory_on_hand"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved"),
        CheckConstraint(
            "reserved_quantity <= quantity_on_hand", name="ck_inventory_available"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, unique=True
    )
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    product: Mapped[ProductModel] = relationship(back_populates="inventory_item")


class OrderModel(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status_approved_at", "status", "approved_at"),
        CheckConstraint("budget_amount >= 0", name="ck_orders_budget"),
        CheckConstraint(
            "order_type IN ('normal', 'donation', 'compensatory')",
            name="ck_orders_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', "
            "'cancelled', 'ready_to_ship')",
            name="ck_orders_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("residents.id"), nullable=False, index=True
    )
    requester_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    budget_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    resident_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    shipping_address_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    partial_fulfillment_approved: Mapped[Optional[bool]] = mapped_column(Boolean)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    resident: Mapped[ResidentModel] = relationship(back_populates="orders")
    requester: Mapped[Optional[UserModel]] = relationship(
        back_populates="requested_orders", foreign_keys=[requester_id]
    )
    items: Mapped[list[OrderItemModel]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItemModel.position"
    )
    invoice: Mapped[Optional[InvoiceModel]] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )
    approvals: Mapped[list[ApprovalModel]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    change_requests: Mapped[list[ChangeRequestModel]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    shipments: Mapped[list[ShipmentModel]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItemModel(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price"),
        UniqueConstraint("order_id", "position", name="uq_order_items_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    product_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("products.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    size_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[OrderModel] = relationship(back_populates="items")
    product: Mapped[Optional[ProductModel]] = relationship(back_populates="order_items")
    inventory_reservation: Mapped[Optional[InventoryReservationModel]] = relationship(
        back_populates="order_item", uselist=False, cascade="all, delete-orphan"
    )


class InvoiceModel(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("item_subtotal >= 0", name="ck_invoices_items"),
        CheckConstraint("shipping_cost >= 0", name="ck_invoices_shipping"),
        CheckConstraint("total >= 0", name="ck_invoices_total"),
        CheckConstraint(
            "total = item_subtotal + shipping_cost", name="ck_invoices_total_calculation"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, unique=True)
    item_subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[OrderModel] = relationship(back_populates="invoice")


class ApprovalModel(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_approvals_decision"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    boss_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)

    order: Mapped[OrderModel] = relationship(back_populates="approvals")
    boss: Mapped[UserModel] = relationship(back_populates="approvals")


class InventoryReservationModel(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_reservations_quantity"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id"), nullable=False, unique=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order_item: Mapped[OrderItemModel] = relationship(back_populates="inventory_reservation")


class ShipmentModel(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'picking', 'labeling', 'packing', 'ready_to_ship', "
            "'cancelled')",
            name="ck_shipments_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ShipmentStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    order: Mapped[OrderModel] = relationship(back_populates="shipments")
    items: Mapped[list[ShipmentItemModel]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class ShipmentItemModel(Base):
    __tablename__ = "shipment_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_shipment_items_quantity"),
        UniqueConstraint("shipment_id", "order_item_id", name="uq_shipment_item"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id"), nullable=False)
    order_item_id: Mapped[UUID] = mapped_column(ForeignKey("order_items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    shipment: Mapped[ShipmentModel] = relationship(back_populates="items")
    order_item: Mapped[OrderItemModel] = relationship()


class ChangeRequestModel(Base):
    __tablename__ = "change_requests"
    __table_args__ = (
        CheckConstraint("request_type IN ('change', 'cancellation')", name="ck_change_type"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_change_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChangeRequestStatus.PENDING.value)
    requested_details: Mapped[Optional[str]] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    boss_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    boss_decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    order: Mapped[OrderModel] = relationship(back_populates="change_requests")
    requester: Mapped[UserModel] = relationship(
        back_populates="requested_change_requests", foreign_keys=[requester_id]
    )
    boss: Mapped[Optional[UserModel]] = relationship(
        back_populates="decided_change_requests", foreign_keys=[boss_id]
    )
