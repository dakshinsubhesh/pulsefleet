"""
PulseFleet — Vehicle create endpoint
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ConflictError
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
) -> models.Vehicle:
    """Create a vehicle. Fails with 409 if plate_number is already registered."""
    existing = await db.execute(
        select(models.Vehicle).where(models.Vehicle.plate_number == payload.plate_number)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A vehicle with plate_number '{payload.plate_number}' already exists.",
            error_code="duplicate_plate_number",
        )

    vehicle = models.Vehicle(**payload.model_dump())
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
