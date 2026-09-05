"""
PulseFleet — Driver endpoints
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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
) -> models.Driver:
    """Create a driver. Fails with 409 if license_number is already registered."""
    existing = await db.execute(
        select(models.Driver).where(models.Driver.license_number == payload.license_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A driver with license_number '{payload.license_number}' already exists.",
            error_code="duplicate_license_number",
        )

    driver = models.Driver(**payload.model_dump())
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
) -> schemas.Page:
    """
    List drivers, ordered by id ascending (stable — a given offset/limit
    always returns the same page regardless of concurrent writes elsewhere).
    """
    total = (await db.execute(select(func.count()).select_from(models.Driver))).scalar_one()
    result = await db.execute(
        select(models.Driver)
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
async def get_driver(driver_id: int, db: AsyncSession = Depends(get_db)) -> models.Driver:
    """Fetch a single driver by id. 404 if it doesn't exist."""
    result = await db.execute(select(models.Driver).where(models.Driver.id == driver_id))
    driver = result.scalar_one_or_none()
    if driver is None:
        raise NotFoundError(f"Driver {driver_id} not found.", error_code="driver_not_found")
    return driver
