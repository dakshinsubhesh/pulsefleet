"""
PulseFleet — Shipment create endpoint (primary create workflow, Day 4)
Day 5: Read workflows — list (filtered, paginated) and detail endpoints

A shipment is created together with its route in a single database
transaction: if either insert fails, nothing is persisted.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.exceptions import ConflictError, NotFoundError
from app.pagination import PaginationParams, pagination_params
from app import models, schemas

router = APIRouter(prefix="/shipments", tags=["shipments"])


async def _get_driver_or_404(db: AsyncSession, driver_id: int) -> None:
    result = await db.execute(select(models.Driver.id).where(models.Driver.id == driver_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")


async def _get_vehicle_or_404(db: AsyncSession, vehicle_id: int) -> None:
    result = await db.execute(select(models.Vehicle.id).where(models.Vehicle.id == vehicle_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found.", error_code="vehicle_not_found")


@router.post(
    "",
    response_model=schemas.ShipmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_shipment(
    payload: schemas.ShipmentCreate,
    db: AsyncSession = Depends(get_db),
) -> models.Shipment:
    """
    Create a shipment and its route in one transaction.

    Validation performed before any write:
    - tracking_number must be unique (409 if taken)
    - driver_id, if given, must reference an existing driver (404)
    - vehicle_id, if given, must reference an existing vehicle (404)
    - field-level validation (weight > 0, priority 1-3, etc.) is enforced
      by ShipmentCreate/RouteCreate via FastAPI's automatic 422 responses

    Success returns 201 with the created shipment (nested route included).
    """
    # --- pre-write validation --------------------------------------------
    existing = await db.execute(
        select(models.Shipment.id).where(models.Shipment.tracking_number == payload.tracking_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A shipment with tracking_number '{payload.tracking_number}' already exists.",
            error_code="duplicate_tracking_number",
        )

    if payload.driver_id is not None:
        await _get_driver_or_404(db, payload.driver_id)
    if payload.vehicle_id is not None:
        await _get_vehicle_or_404(db, payload.vehicle_id)

    # --- transactional write ----------------------------------------------
    # Both inserts happen on the same session/transaction. If the Route
    # insert fails (e.g. a constraint violation), the whole transaction
    # rolls back and no orphaned Shipment row is left behind.
    shipment_data = payload.model_dump(exclude={"route"})
    shipment = models.Shipment(**shipment_data)
    shipment.route = models.Route(**payload.route.model_dump())

    db.add(shipment)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            "Could not create shipment due to a data conflict (duplicate tracking number "
            "or invalid reference).",
            error_code="shipment_write_conflict",
        )

    # Re-fetch with route eagerly loaded so the response doesn't trigger
    # a lazy-load outside the session (and so serialization is predictable).
    result = await db.execute(
        select(models.Shipment)
        .options(selectinload(models.Shipment.route))
        .where(models.Shipment.id == shipment.id)
    )
    return result.scalar_one()


@router.get("", response_model=schemas.Page[schemas.ShipmentResponse])
async def list_shipments(
    status_filter: Optional[models.ShipmentStatus] = Query(default=None, alias="status"),
    driver_id: Optional[int] = Query(default=None),
    vehicle_id: Optional[int] = Query(default=None),
    priority: Optional[int] = Query(default=None, ge=1, le=3),
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page:
    """
    List shipments, optionally filtered by status/driver_id/vehicle_id/priority.

    Ordered by id ascending — a stable sort key (the primary key never
    changes), so a given offset/limit returns a consistent page even if
    other shipments are being created concurrently. `created_at` alone
    isn't used as the sort key since two rows can share a timestamp.
    """
    filters = []
    if status_filter is not None:
        filters.append(models.Shipment.status == status_filter)
    if driver_id is not None:
        filters.append(models.Shipment.driver_id == driver_id)
    if vehicle_id is not None:
        filters.append(models.Shipment.vehicle_id == vehicle_id)
    if priority is not None:
        filters.append(models.Shipment.priority == priority)

    count_stmt = select(func.count()).select_from(models.Shipment)
    list_stmt = select(models.Shipment).options(selectinload(models.Shipment.route))
    for f in filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()
    result = await db.execute(
        list_stmt.order_by(models.Shipment.id.asc()).limit(pagination.limit).offset(pagination.offset)
    )
    return schemas.Page(
        items=list(result.scalars().all()),
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{shipment_id}", response_model=schemas.ShipmentResponse)
async def get_shipment(shipment_id: int, db: AsyncSession = Depends(get_db)) -> models.Shipment:
    """Fetch a single shipment (with its route) by id. 404 if it doesn't exist."""
    result = await db.execute(
        select(models.Shipment)
        .options(selectinload(models.Shipment.route))
        .where(models.Shipment.id == shipment_id)
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(f"Shipment {shipment_id} not found.", error_code="shipment_not_found")
    return shipment
