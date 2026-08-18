"""Small FastAPI interface for the V0 catalog and draft-order workflow."""

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from .database import create_database_engine, create_session_factory
from .db_models import FacilityModel, OrderModel, ResidentModel, UserModel, UserRole
from .orders import Order, OrderType, Product
from .repositories import OrderRepository, OrderWorkflowRepository, ProductRepository


class FacilityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    shipping_address: str = Field(min_length=1)


class FacilityResponse(FacilityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


class ResidentCreate(BaseModel):
    facility_id: UUID
    full_name: str = Field(min_length=1, max_length=200)


class ResidentResponse(ResidentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool


class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    role: UserRole
    facility_id: UUID | None = None


class UserResponse(UserCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    size: str = Field(min_length=1, max_length=50)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    is_active: bool = True


class ProductResponse(ProductCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


class OrderCreate(BaseModel):
    resident_id: UUID
    requester_id: UUID
    budget_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    order_type: OrderType = OrderType.NORMAL


class OrderItemCreate(BaseModel):
    product_sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0)


class ConfirmOrderRequest(BaseModel):
    shipping_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ApproveOrderRequest(BaseModel):
    boss_id: UUID
    allow_partial_fulfillment: bool


class RejectOrderRequest(BaseModel):
    boss_id: UUID
    reason: str | None = None


class OrderItemResponse(BaseModel):
    id: UUID
    sku: str
    product_name: str
    size: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class InvoiceResponse(BaseModel):
    item_subtotal: Decimal
    shipping_cost: Decimal
    total: Decimal


class OrderResponse(BaseModel):
    id: UUID
    resident_id: UUID
    requester_id: UUID | None
    order_type: str
    status: str
    budget_amount: Decimal
    resident_name: str
    shipping_address: str
    item_subtotal: Decimal
    total: Decimal
    items: list[OrderItemResponse]
    invoice: InvoiceResponse | None


engine = create_database_engine()
SessionFactory = create_session_factory(engine)
app = FastAPI(title="Residential Clothing Automation API", version="0.1.0")


def get_session() -> Generator[Session, None, None]:
    """Provide one transaction per request using the configured PostgreSQL database."""

    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.exception_handler(ValueError)
async def domain_validation_error(_request, error: ValueError) -> JSONResponse:
    """Return domain and repository validation errors as clear client responses."""

    detail = str(error)
    conflict_prefixes = ("Only draft orders", "Order is incomplete", "Only orders pending")
    response_status = status.HTTP_409_CONFLICT if detail.startswith(conflict_prefixes) else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=response_status, content={"detail": detail})


def _require_model(session: Session, model_type, model_id: UUID, label: str):
    model = session.get(model_type, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown {label}: {model_id}")
    return model


def _product_response(product) -> ProductResponse:
    return ProductResponse.model_validate(product)


def _order_response(order: OrderModel) -> OrderResponse:
    items = [
        OrderItemResponse(
            id=item.id,
            sku=item.sku_snapshot,
            product_name=item.product_name_snapshot,
            size=item.size_snapshot,
            unit_price=item.unit_price,
            quantity=item.quantity,
            subtotal=item.unit_price * item.quantity,
        )
        for item in order.items
    ]
    item_subtotal = sum((item.subtotal for item in items), Decimal("0.00"))
    invoice = (
        InvoiceResponse(
            item_subtotal=order.invoice.item_subtotal,
            shipping_cost=order.invoice.shipping_cost,
            total=order.invoice.total,
        )
        if order.invoice
        else None
    )
    return OrderResponse(
        id=order.id,
        resident_id=order.resident_id,
        requester_id=order.requester_id,
        order_type=order.order_type,
        status=order.status,
        budget_amount=order.budget_amount,
        resident_name=order.resident_name_snapshot,
        shipping_address=order.shipping_address_snapshot,
        item_subtotal=item_subtotal,
        total=invoice.total if invoice else item_subtotal,
        items=items,
        invoice=invoice,
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return a simple application-process health response."""

    return {"status": "ok"}


@app.post("/facilities", response_model=FacilityResponse, status_code=status.HTTP_201_CREATED)
def create_facility(payload: FacilityCreate, session: Session = Depends(get_session)) -> FacilityModel:
    facility = FacilityModel(**payload.model_dump())
    session.add(facility)
    session.flush()
    return facility


@app.post("/residents", response_model=ResidentResponse, status_code=status.HTTP_201_CREATED)
def create_resident(payload: ResidentCreate, session: Session = Depends(get_session)) -> ResidentModel:
    _require_model(session, FacilityModel, payload.facility_id, "facility")
    resident = ResidentModel(**payload.model_dump())
    session.add(resident)
    session.flush()
    return resident


@app.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, session: Session = Depends(get_session)) -> UserModel:
    if payload.role is UserRole.AUTHORIZED_REQUESTER and payload.facility_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An authorized requester must belong to a facility",
        )
    if payload.facility_id is not None:
        _require_model(session, FacilityModel, payload.facility_id, "facility")
    user = UserModel(
        full_name=payload.full_name,
        role=payload.role.value,
        facility_id=payload.facility_id,
    )
    session.add(user)
    session.flush()
    return user


@app.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, session: Session = Depends(get_session)) -> ProductResponse:
    try:
        product = Product(**payload.model_dump())
        saved = ProductRepository().add(session, product)
    except ValueError as error:
        if str(error).startswith("Product SKU already exists"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        raise
    return _product_response(saved)


@app.get("/products", response_model=list[ProductResponse])
def list_products(session: Session = Depends(get_session)) -> list[ProductResponse]:
    return [_product_response(product) for product in ProductRepository.list_models(session)]


@app.get("/products/{sku}", response_model=ProductResponse)
def get_product(sku: str, session: Session = Depends(get_session)) -> ProductResponse:
    try:
        return _product_response(ProductRepository().get_model(session, sku))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, session: Session = Depends(get_session)) -> OrderResponse:
    resident = _require_model(session, ResidentModel, payload.resident_id, "resident")
    requester = _require_model(session, UserModel, payload.requester_id, "requester")
    order = Order(budget_amount=payload.budget_amount, order_type=payload.order_type)
    saved = OrderRepository().add(session, order, resident=resident, requester=requester)
    return _order_response(OrderRepository.get_model(session, saved.id))


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: UUID, session: Session = Depends(get_session)) -> OrderResponse:
    try:
        return _order_response(OrderRepository.get_model(session, order_id))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@app.post("/orders/{order_id}/items", response_model=OrderResponse)
def add_order_item(
    order_id: UUID, payload: OrderItemCreate, session: Session = Depends(get_session)
) -> OrderResponse:
    try:
        order = OrderRepository().add_item_to_draft(
            session, order_id, product_sku=payload.product_sku, quantity=payload.quantity
        )
    except ValueError as error:
        if str(error).startswith("Unknown order") or str(error).startswith("Unknown product"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _order_response(order)


@app.post("/orders/{order_id}/confirm", response_model=OrderResponse)
def confirm_order(
    order_id: UUID, payload: ConfirmOrderRequest, session: Session = Depends(get_session)
) -> OrderResponse:
    try:
        order = OrderRepository().confirm_draft(
            session, order_id, shipping_cost=payload.shipping_cost
        )
    except ValueError as error:
        if str(error).startswith("Unknown order"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _order_response(order)


@app.post("/orders/{order_id}/approve", response_model=OrderResponse)
def approve_order(
    order_id: UUID, payload: ApproveOrderRequest, session: Session = Depends(get_session)
) -> OrderResponse:
    boss = _require_model(session, UserModel, payload.boss_id, "Boss")
    try:
        order = OrderWorkflowRepository().approve_order(
            session,
            order_id,
            boss=boss,
            allow_partial_fulfillment=payload.allow_partial_fulfillment,
        )
    except ValueError as error:
        if str(error).startswith("Unknown order"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _order_response(OrderRepository.get_model(session, order.id))


@app.post("/orders/{order_id}/reject", response_model=OrderResponse)
def reject_order(
    order_id: UUID, payload: RejectOrderRequest, session: Session = Depends(get_session)
) -> OrderResponse:
    boss = _require_model(session, UserModel, payload.boss_id, "Boss")
    try:
        order = OrderWorkflowRepository().reject_order(
            session, order_id, boss=boss, reason=payload.reason
        )
    except ValueError as error:
        if str(error).startswith("Unknown order"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _order_response(OrderRepository.get_model(session, order.id))
