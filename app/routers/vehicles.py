"""
PulseFleet — Vehicle endpoints
Day 8: Authorization — every query is scoped to the authenticated
owner. A vehicle that belongs to another user is indistinguishable
from one that doesn't exist: both return 404.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import ConflictError, NotFoundError
from app.pagination import PaginationParams, pagination_params
from app import models, schemas

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.post(
    "",
    response_model=schemas.VehicleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_vehicle(
    payload: schemas.VehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Vehicle:
    """Create a vehicle owned by the caller. Fails with 409 if plate_number is already registered."""
    existing = await db.execute(
        select(models.Vehicle).where(models.Vehicle.plate_number == payload.plate_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A vehicle with plate_number '{payload.plate_number}' already exists.",
            error_code="duplicate_plate_number",
        )

    vehicle = models.Vehicle(owner_id=current_user.id, **payload.model_dump())
    db.add(vehicle)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            f"A vehicle with plate_number '{payload.plate_number}' already exists.",
            error_code="duplicate_plate_number",
        )
    await db.refresh(vehicle)
    return vehicle


@router.get("", response_model=schemas.Page[schemas.VehicleResponse])
async def list_vehicles(
    q: Optional[str] = Query(
        default=None, min_length=1, max_length=100,
        description="Case-insensitive substring match on plate_number or vehicle_type",
    ),
    status_filter: Optional[models.VehicleStatus] = Query(default=None, alias="status"),
    vehicle_type: Optional[str] = Query(default=None, min_length=1, max_length=30),
    capacity_min: Optional[float] = Query(default=None, ge=0),
    capacity_max: Optional[float] = Query(default=None, ge=0),
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.Page:
    """
    List the caller's own vehicles, ordered by id ascending. Owner-scoped —
    every filter below combines with the owner check via SQL AND, so
    nothing is fetched into Python before filtering.

    - `q` — case-insensitive substring search on plate_number/vehicle_type (ILIKE)
    - `status` — exact match
    - `vehicle_type` — exact match (use `q` for partial matching)
    - `capacity_min`/`capacity_max` — inclusive range on capacity_kg
    """
    if capacity_min is not None and capacity_max is not None and capacity_min > capacity_max:
        return JSONResponse(
            status_code=422,
            content={"detail": "capacity_min cannot be greater than capacity_max.", "error_code": "invalid_range"},
        )

    filters = [models.Vehicle.owner_id == current_user.id]
    if status_filter is not None:
        filters.append(models.Vehicle.status == status_filter)
    if vehicle_type is not None:
        filters.append(models.Vehicle.vehicle_type == vehicle_type)
    if capacity_min is not None:
        filters.append(models.Vehicle.capacity_kg >= capacity_min)
    if capacity_max is not None:
        filters.append(models.Vehicle.capacity_kg <= capacity_max)
    if q is not None:
        pattern = f"%{q}%"
        filters.append(models.Vehicle.plate_number.ilike(pattern) | models.Vehicle.vehicle_type.ilike(pattern))

    count_stmt = select(func.count()).select_from(models.Vehicle)
    list_stmt = select(models.Vehicle)
    for f in filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()
    result = await db.execute(
        list_stmt.order_by(models.Vehicle.id.asc()).limit(pagination.limit).offset(pagination.offset)
    )
    return schemas.Page(
        items=list(result.scalars().all()),
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Vehicle:
    """Fetch a single vehicle by id — only if it belongs to the caller. 404 otherwise."""
    result = await db.execute(
        select(models.Vehicle).where(models.Vehicle.id == vehicle_id, models.Vehicle.owner_id == current_user.id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found.", error_code="vehicle_not_found")
    return vehicle


@router.patch("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def update_vehicle(
    vehicle_id: int,
    payload: schemas.VehicleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Vehicle:
    """Partially update a vehicle — only if it belongs to the caller."""
    result = await db.execute(
        select(models.Vehicle).where(models.Vehicle.id == vehicle_id, models.Vehicle.owner_id == current_user.id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found.", error_code="vehicle_not_found")

    updates = payload.model_dump(exclude_unset=True)

    if "plate_number" in updates and updates["plate_number"] != vehicle.plate_number:
        existing = await db.execute(
            select(models.Vehicle.id).where(models.Vehicle.plate_number == updates["plate_number"])
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                f"A vehicle with plate_number '{updates['plate_number']}' already exists.",
                error_code="duplicate_plate_number",
            )

    for field, value in updates.items():
        setattr(vehicle, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("Could not update vehicle due to a data conflict.", error_code="vehicle_write_conflict")
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    """Delete a vehicle — only if it belongs to the caller, and only if it has no active shipments."""
    result = await db.execute(
        select(models.Vehicle).where(models.Vehicle.id == vehicle_id, models.Vehicle.owner_id == current_user.id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found.", error_code="vehicle_not_found")

    active_count = (
        await db.execute(
            select(func.count())
            .select_from(models.Shipment)
            .where(
                models.Shipment.vehicle_id == vehicle_id,
                models.Shipment.status.in_(
                    [models.ShipmentStatus.pending, models.ShipmentStatus.in_transit]
                ),
            )
        )
    ).scalar_one()
    if active_count > 0:
        raise ConflictError(
            f"Vehicle {vehicle_id} has {active_count} active shipment(s) and cannot be deleted. "
            "Reassign or resolve them first.",
            error_code="vehicle_has_active_shipments",
        )

    await db.delete(vehicle)
    await db.commit()
    return None
