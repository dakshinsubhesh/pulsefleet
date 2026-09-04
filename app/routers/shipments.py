"""
PulseFleet — Shipment create endpoint (primary create workflow, Day 4)

A shipment is created together with its route in a single database
transaction: if either insert fails, nothing is persisted.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.exceptions import ConflictError, NotFoundError
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
