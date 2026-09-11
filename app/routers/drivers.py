"""
PulseFleet — Driver endpoints
Day 8: Authorization — every query is scoped to the authenticated
owner, so no user can read, list, update, or delete another user's
drivers. A driver that exists but belongs to someone else looks
identical to a driver that doesn't exist: both return 404. This is
deliberate — it avoids leaking which ids are in use by other tenants.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import ConflictError, NotFoundError
from app.pagination import PaginationParams, pagination_params
from app import models, schemas

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post(
    "",
    response_model=schemas.DriverResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_driver(
    payload: schemas.DriverCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Driver:
    """Create a driver owned by the caller. Fails with 409 if license_number is already registered."""
    existing = await db.execute(
        select(models.Driver).where(models.Driver.license_number == payload.license_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A driver with license_number '{payload.license_number}' already exists.",
            error_code="duplicate_license_number",
        )

    driver = models.Driver(owner_id=current_user.id, **payload.model_dump())
    db.add(driver)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            f"A driver with license_number '{payload.license_number}' already exists.",
            error_code="duplicate_license_number",
        )
    await db.refresh(driver)
    return driver


@router.get("", response_model=schemas.Page[schemas.DriverResponse])
async def list_drivers(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.Page:
    """
    List the caller's own drivers, ordered by id ascending (stable
    pagination). Owner-scoped: another user's drivers never appear here,
    at any offset.
    """
    owner_filter = models.Driver.owner_id == current_user.id
    total = (
        await db.execute(select(func.count()).select_from(models.Driver).where(owner_filter))
    ).scalar_one()
    result = await db.execute(
        select(models.Driver)
        .where(owner_filter)
        .order_by(models.Driver.id.asc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    return schemas.Page(
        items=list(result.scalars().all()),
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{driver_id}", response_model=schemas.DriverResponse)
async def get_driver(
    driver_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Driver:
    """
    Fetch a single driver by id — but only if it belongs to the caller.
    404 both when the id doesn't exist at all, and when it belongs to
    another user.
    """
    result = await db.execute(
        select(models.Driver).where(models.Driver.id == driver_id, models.Driver.owner_id == current_user.id)
    )
    driver = result.scalar_one_or_none()
    if driver is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")
    return driver


@router.patch("/{driver_id}", response_model=schemas.DriverResponse)
async def update_driver(
    driver_id: int,
    payload: schemas.DriverUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Driver:
    """Partially update a driver — only if it belongs to the caller."""
    result = await db.execute(
        select(models.Driver).where(models.Driver.id == driver_id, models.Driver.owner_id == current_user.id)
    )
    driver = result.scalar_one_or_none()
    if driver is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")

    updates = payload.model_dump(exclude_unset=True)

    if "license_number" in updates and updates["license_number"] != driver.license_number:
        existing = await db.execute(
            select(models.Driver.id).where(models.Driver.license_number == updates["license_number"])
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                f"A driver with license_number '{updates['license_number']}' already exists.",
                error_code="duplicate_license_number",
            )

    for field, value in updates.items():
        setattr(driver, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("Could not update driver due to a data conflict.", error_code="driver_write_conflict")
    await db.refresh(driver)
    return driver


@router.delete("/{driver_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_driver(
    driver_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    """
    Delete a driver — only if it belongs to the caller, and only if it
    has no active (pending/in_transit) shipments.
    """
    result = await db.execute(
        select(models.Driver).where(models.Driver.id == driver_id, models.Driver.owner_id == current_user.id)
    )
    driver = result.scalar_one_or_none()
    if driver is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")

    active_count = (
        await db.execute(
            select(func.count())
            .select_from(models.Shipment)
            .where(
                models.Shipment.driver_id == driver_id,
                models.Shipment.status.in_(
                    [models.ShipmentStatus.pending, models.ShipmentStatus.in_transit]
                ),
            )
        )
    ).scalar_one()
    if active_count > 0:
        raise ConflictError(
            f"Driver {driver_id} has {active_count} active shipment(s) and cannot be deleted. "
            "Reassign or resolve them first.",
            error_code="driver_has_active_shipments",
        )

    await db.delete(driver)
    await db.commit()
    return None
