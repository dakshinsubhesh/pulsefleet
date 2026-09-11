"""
PulseFleet — Shipment endpoints
Day 4: Create workflow
Day 5: Read workflows — list (filtered, paginated) and detail
Day 6: Update and delete workflows
Day 7: Authentication — writes require a valid Bearer token
Day 8: Authorization — every query (including reads) is scoped to the
authenticated owner. A shipment belonging to another user returns the
same 404 as one that doesn't exist at all, so cross-user access can't
be used to probe which ids are in use.

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
from app.dependencies import get_current_user
from app.exceptions import ConflictError, NotFoundError
from app.pagination import PaginationParams, pagination_params
from app.shipment_state import is_terminal, is_valid_transition
from app import models, schemas

router = APIRouter(prefix="/shipments", tags=["shipments"])


async def _get_driver_or_404(db: AsyncSession, driver_id: int, owner_id: int) -> None:
    """A driver reference is only valid if it exists AND belongs to the caller."""
    result = await db.execute(
        select(models.Driver.id).where(models.Driver.id == driver_id, models.Driver.owner_id == owner_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")


async def _get_vehicle_or_404(db: AsyncSession, vehicle_id: int, owner_id: int) -> None:
    """A vehicle reference is only valid if it exists AND belongs to the caller."""
    result = await db.execute(
        select(models.Vehicle.id).where(models.Vehicle.id == vehicle_id, models.Vehicle.owner_id == owner_id)
    )
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
    current_user: models.User = Depends(get_current_user),
) -> models.Shipment:
    """
    Create a shipment (owned by the caller) and its route in one transaction.

    Validation before any write:
    - tracking_number must be unique (409 if taken)
    - driver_id / vehicle_id, if given, must reference rows that exist
      AND belong to the caller (404 otherwise — you can't assign someone
      else's driver or vehicle to your shipment)
    - field-level validation enforced by ShipmentCreate/RouteCreate (422)
    """
    existing = await db.execute(
        select(models.Shipment.id).where(models.Shipment.tracking_number == payload.tracking_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A shipment with tracking_number '{payload.tracking_number}' already exists.",
            error_code="duplicate_tracking_number",
        )

    if payload.driver_id is not None:
        await _get_driver_or_404(db, payload.driver_id, current_user.id)
    if payload.vehicle_id is not None:
        await _get_vehicle_or_404(db, payload.vehicle_id, current_user.id)

    shipment_data = payload.model_dump(exclude={"route"})
    shipment = models.Shipment(owner_id=current_user.id, **shipment_data)
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
    current_user: models.User = Depends(get_current_user),
) -> schemas.Page:
    """
    List the caller's own shipments, optionally filtered further by
    status/driver_id/vehicle_id/priority. Owner-scoped: another user's
    shipments never appear, at any filter combination or offset.

    Ordered by id ascending — a stable sort key — so a given offset/limit
    returns a consistent page even with concurrent writes.
    """
    filters = [models.Shipment.owner_id == current_user.id]
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
async def get_shipment(
    shipment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Shipment:
    """
    Fetch a single shipment by id — only if it belongs to the caller.
    404 both when the id doesn't exist, and when it belongs to another user.
    """
    result = await db.execute(
        select(models.Shipment)
        .options(selectinload(models.Shipment.route))
        .where(models.Shipment.id == shipment_id, models.Shipment.owner_id == current_user.id)
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(f"Shipment {shipment_id} not found.", error_code="shipment_not_found")
    return shipment


@router.patch("/{shipment_id}", response_model=schemas.ShipmentResponse)
async def update_shipment(
    shipment_id: int,
    payload: schemas.ShipmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Shipment:
    """
    Partially update a shipment — only if it belongs to the caller.
    Only fields present in the request body are changed.

    Validation before any write:
    - the shipment must belong to the caller (404 otherwise)
    - driver_id / vehicle_id, if changed, must reference rows that exist
      AND belong to the caller (404)
    - status changes must follow the allowed lifecycle; delivered/cancelled
      are terminal (409 on any attempted change)
    - field-level constraints enforced by ShipmentUpdate (422)
    """
    result = await db.execute(
        select(models.Shipment)
        .options(selectinload(models.Shipment.route))
        .where(models.Shipment.id == shipment_id, models.Shipment.owner_id == current_user.id)
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(f"Shipment {shipment_id} not found.", error_code="shipment_not_found")

    updates = payload.model_dump(exclude_unset=True)

    if "driver_id" in updates and updates["driver_id"] is not None:
        await _get_driver_or_404(db, updates["driver_id"], current_user.id)
    if "vehicle_id" in updates and updates["vehicle_id"] is not None:
        await _get_vehicle_or_404(db, updates["vehicle_id"], current_user.id)

    if "status" in updates:
        new_status = models.ShipmentStatus(updates["status"])
        updates["status"] = new_status
        if is_terminal(shipment.status):
            raise ConflictError(
                f"Shipment {shipment_id} is already '{shipment.status.value}' and cannot be "
                "updated further.",
                error_code="shipment_terminal_state",
            )
        if not is_valid_transition(shipment.status, new_status):
            raise ConflictError(
                f"Cannot move shipment from '{shipment.status.value}' to '{new_status.value}'.",
                error_code="invalid_status_transition",
            )
    elif is_terminal(shipment.status) and updates:
        raise ConflictError(
            f"Shipment {shipment_id} is '{shipment.status.value}' and is closed to edits.",
            error_code="shipment_terminal_state",
        )

    for field, value in updates.items():
        setattr(shipment, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            "Could not update shipment due to a data conflict.",
            error_code="shipment_write_conflict",
        )

    result = await db.execute(
        select(models.Shipment)
        .options(selectinload(models.Shipment.route))
        .where(models.Shipment.id == shipment_id)
    )
    return result.scalar_one()


@router.delete("/{shipment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shipment(
    shipment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    """
    Delete a shipment — only if it belongs to the caller, and only while
    it's still 'pending' (anything further along must be cancelled via
    PATCH instead of erased). Cascades to its Route/Alerts.
    """
    result = await db.execute(
        select(models.Shipment).where(
            models.Shipment.id == shipment_id, models.Shipment.owner_id == current_user.id
        )
    )
    shipment = result.scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(f"Shipment {shipment_id} not found.", error_code="shipment_not_found")

    if shipment.status != models.ShipmentStatus.pending:
        raise ConflictError(
            f"Shipment {shipment_id} is '{shipment.status.value}' and cannot be deleted; "
            "only 'pending' shipments can be deleted. Cancel it instead via PATCH.",
            error_code="shipment_not_deletable",
        )

    await db.delete(shipment)
    await db.commit()
    return None
