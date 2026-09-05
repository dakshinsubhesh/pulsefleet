"""
PulseFleet — Vehicle endpoints
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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


@router.get("", response_model=schemas.Page[schemas.VehicleResponse])
async def list_vehicles(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page:
    """List vehicles, ordered by id ascending (stable pagination)."""
    total = (await db.execute(select(func.count()).select_from(models.Vehicle))).scalar_one()
    result = await db.execute(
        select(models.Vehicle)
        .order_by(models.Vehicle.id.asc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    return schemas.Page(
        items=list(result.scalars().all()),
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def get_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db)) -> models.Vehicle:
    """Fetch a single vehicle by id. 404 if it doesn't exist."""
    result = await db.execute(select(models.Vehicle).where(models.Vehicle.id == vehicle_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} not found.", error_code="vehicle_not_found")
    return vehicle
