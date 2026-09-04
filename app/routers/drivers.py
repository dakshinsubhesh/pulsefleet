"""
PulseFleet — Driver create endpoint
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ConflictError
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
