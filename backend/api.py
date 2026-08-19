"""Small FastAPI interface for the V0 catalog and draft-order workflow."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from .database import create_database_engine, create_session_factory
from .db_models import (
    FacilityModel,
    InventoryItemModel,
    OrderModel,
    ResidentModel,
    ShipmentModel,
    ShipmentStatus,
    UserModel,
    UserRole,
)
from .orders import Order, OrderType, Product
from .repositories import (
    InventoryRepository,
    OrderRepository,
    OrderWorkflowRepository,
    ProductRepository,
    ShipmentRepository,
)
from .reliability import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyRepository,
    configure_application_logging,
    logger,
    retry_transient_database_operation,
)
from .workflows import FulfillmentAutomation


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


class InventoryStockRequest(BaseModel):
    quantity: int = Field(gt=0)


class InventoryResponse(BaseModel):
    product_sku: str
    quantity_on_hand: int
    reserved_quantity: int
    available_quantity: int


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


class AdvanceFulfillmentTaskRequest(BaseModel):
    next_status: ShipmentStatus


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


class FulfillmentTaskItemResponse(BaseModel):
    order_item_id: UUID
    sku: str
    product_name: str
    quantity: int


class FulfillmentTaskResponse(BaseModel):
    id: UUID
    order_id: UUID
    status: ShipmentStatus
    created_at: datetime
    items: list[FulfillmentTaskItemResponse]


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
configure_application_logging()
app = FastAPI(title="Residential Clothing Automation API", version="0.2.0")
app.state.idempotency_session_factory = SessionFactory


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


async def _buffer_response(response: Response) -> Response:
    """Read a response once so a completed POST can be replayed exactly."""

    body = b"".join([chunk async for chunk in response.body_iterator])
    return Response(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )


@app.middleware("http")
async def reliability_middleware(request: Request, call_next) -> Response:
    """Attach request logs and optional durable idempotency to POST requests."""

    started_at = perf_counter()
    request_id = request.headers.get("X-Request-ID", str(uuid4()))[:100]
    idempotency_key = request.headers.get("Idempotency-Key")
    idempotency_repository: IdempotencyRepository | None = None

    if request.method == "POST" and idempotency_key is not None:
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            response = JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Idempotency-Key must contain 1 to 255 characters"},
            )
            response.headers["X-Request-ID"] = request_id
            return response

        body = await request.body()
        request_hash = sha256(body).hexdigest()
        idempotency_repository = IdempotencyRepository(
            request.app.state.idempotency_session_factory
        )
        try:
            replay = retry_transient_database_operation(
                lambda: idempotency_repository.begin(
                    key=idempotency_key,
                    method=request.method,
                    path=request.url.path,
                    request_hash=request_hash,
                ),
                operation_name="idempotency_begin",
            )
        except IdempotencyConflictError as error:
            response = JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(error)})
            response.headers["X-Request-ID"] = request_id
            return response
        except IdempotencyInProgressError as error:
            response = JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(error)})
            response.headers["X-Request-ID"] = request_id
            return response

        if replay is not None:
            response = Response(
                content=replay.body,
                status_code=replay.status_code,
                media_type=replay.content_type or "application/json",
            )
            response.headers["X-Idempotency-Replayed"] = "true"
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_replayed request_id=%s method=%s path=%s status=%s",
                request_id,
                request.method,
                request.url.path,
                replay.status_code,
            )
            return response

    try:
        response = await call_next(request)
    except Exception:
        if idempotency_repository is not None and idempotency_key is not None:
            try:
                retry_transient_database_operation(
                    lambda: idempotency_repository.complete(
                        key=idempotency_key,
                        response_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        response_body='{"detail":"Internal Server Error"}',
                        content_type="application/json",
                    ),
                    operation_name="idempotency_fail",
                )
            except Exception:
                logger.exception("idempotency_failure_recording_failed request_id=%s", request_id)
        logger.exception(
            "request_failed request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise

    response = await _buffer_response(response)
    if idempotency_repository is not None and idempotency_key is not None:
        response_body = response.body.decode("utf-8")
        try:
            retry_transient_database_operation(
                lambda: idempotency_repository.complete(
                    key=idempotency_key,
                    response_status=response.status_code,
                    response_body=response_body,
                    content_type=response.headers.get("content-type"),
                ),
                operation_name="idempotency_complete",
            )
        except Exception:
            logger.exception("idempotency_completion_failed request_id=%s", request_id)

    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (perf_counter() - started_at) * 1000,
    )
    return response


@app.exception_handler(ValueError)
async def domain_validation_error(_request, error: ValueError) -> JSONResponse:
    """Return domain and repository validation errors as clear client responses."""

    detail = str(error)
    conflict_prefixes = (
        "Only draft orders",
        "Order is incomplete",
        "Only orders pending",
        "Shipment status must",
    )
    response_status = status.HTTP_409_CONFLICT if detail.startswith(conflict_prefixes) else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=response_status, content={"detail": detail})


def _require_model(session: Session, model_type, model_id: UUID, label: str):
    model = session.get(model_type, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown {label}: {model_id}")
    return model


def _product_response(product) -> ProductResponse:
    return ProductResponse.model_validate(product)


def _inventory_response(product_sku: str, inventory: InventoryItemModel | None) -> InventoryResponse:
    quantity_on_hand = inventory.quantity_on_hand if inventory else 0
    reserved_quantity = inventory.reserved_quantity if inventory else 0
    return InventoryResponse(
        product_sku=product_sku,
        quantity_on_hand=quantity_on_hand,
        reserved_quantity=reserved_quantity,
        available_quantity=quantity_on_hand - reserved_quantity,
    )


def _fulfillment_task_response(task: ShipmentModel) -> FulfillmentTaskResponse:
    return FulfillmentTaskResponse(
        id=task.id,
        order_id=task.order_id,
        status=ShipmentStatus(task.status),
        created_at=task.created_at,
        items=[
            FulfillmentTaskItemResponse(
                order_item_id=item.order_item_id,
                sku=item.order_item.sku_snapshot,
                product_name=item.order_item.product_name_snapshot,
                quantity=item.quantity,
            )
            for item in task.items
        ],
    )


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


@app.post("/products/{sku}/inventory", response_model=InventoryResponse)
def stock_product(
    sku: str, payload: InventoryStockRequest, session: Session = Depends(get_session)
) -> InventoryResponse:
    """Receive stock and automatically allocate approved orders by priority."""

    try:
        product = ProductRepository().get_model(session, sku)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    inventory = InventoryRepository().stock_product(session, product, payload.quantity)
    FulfillmentAutomation().process(session)
    return _inventory_response(sku, inventory)


@app.get("/products/{sku}/inventory", response_model=InventoryResponse)
def get_inventory(sku: str, session: Session = Depends(get_session)) -> InventoryResponse:
    """Return current stock, reservations, and unreserved available quantity."""

    try:
        product = ProductRepository().get_model(session, sku)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return _inventory_response(sku, product.inventory_item)


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
        FulfillmentAutomation().process(session)
    except ValueError as error:
        if str(error).startswith("Unknown order"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _order_response(OrderRepository.get_model(session, order.id))


@app.get("/orders/{order_id}/fulfillment-tasks", response_model=list[FulfillmentTaskResponse])
def list_fulfillment_tasks(
    order_id: UUID, session: Session = Depends(get_session)
) -> list[FulfillmentTaskResponse]:
    """List the automatically created warehouse tasks for one order."""

    _require_model(session, OrderModel, order_id, "order")
    tasks = ShipmentRepository.list_for_order(session, order_id)
    return [_fulfillment_task_response(task) for task in tasks]


@app.post("/fulfillment-tasks/{task_id}/advance", response_model=FulfillmentTaskResponse)
def advance_fulfillment_task(
    task_id: UUID,
    payload: AdvanceFulfillmentTaskRequest,
    session: Session = Depends(get_session),
) -> FulfillmentTaskResponse:
    """Advance warehouse work through picking, labeling, packing, and ready to ship."""

    try:
        task = ShipmentRepository().advance_status(session, task_id, payload.next_status)
    except ValueError as error:
        if str(error).startswith("Unknown shipment"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        raise
    return _fulfillment_task_response(task)


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
