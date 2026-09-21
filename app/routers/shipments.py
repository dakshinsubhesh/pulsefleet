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
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.pagination import PaginationParams, pagination_params
from app.services.weather_service import RiskLevel, WeatherService, get_weather_service
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
    search: str | None = Query(default=None, min_length=1, max_length=100, description="Search tracking number, origin, or destination"),
    status_filter: Optional[models.ShipmentStatus] = Query(default=None, alias="status"),
    driver_id: Optional[int] = Query(default=None, gt=0),
    vehicle_id: Optional[int] = Query(default=None, gt=0),
    priority: Optional[int] = Query(default=None, ge=1, le=3),
    min_weight_kg: Optional[float] = Query(default=None, gt=0),
    max_weight_kg: Optional[float] = Query(default=None, gt=0),
    weight_min: Optional[float] = Query(default=None, ge=0),
    weight_max: Optional[float] = Query(default=None, ge=0),
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.Page:
    """List owned shipments with SQL-side search/filtering and stable pagination."""
    effective_weight_min = weight_min if weight_min is not None else min_weight_kg
    effective_weight_max = weight_max if weight_max is not None else max_weight_kg

    if effective_weight_min is not None and effective_weight_max is not None and effective_weight_min > effective_weight_max and effective_weight_min > effective_weight_max:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "min_weight_kg cannot be greater than max_weight_kg.",
                "error_code": "invalid_weight_range",
            },
        )

    filters = [models.Shipment.owner_id == current_user.id]
    if status_filter is not None:
        filters.append(models.Shipment.status == status_filter)
    if driver_id is not None:
        filters.append(models.Shipment.driver_id == driver_id)
    if vehicle_id is not None:
        filters.append(models.Shipment.vehicle_id == vehicle_id)
    if priority is not None:
        filters.append(models.Shipment.priority == priority)
    if min_weight_kg is not None:
        filters.append(models.Shipment.weight_kg >= effective_weight_min)
    if max_weight_kg is not None:
        filters.append(models.Shipment.weight_kg <= effective_weight_max)

    list_stmt = select(models.Shipment).options(selectinload(models.Shipment.route)).where(*filters)
    count_stmt = select(func.count()).select_from(models.Shipment).where(*filters)

    if search is not None:
        term = f"%{search.strip()}%"
        search_filter = (
            models.Shipment.tracking_number.ilike(term)
            | models.Route.origin.ilike(term)
            | models.Route.destination.ilike(term)
        )
        list_stmt = list_stmt.join(models.Route).where(search_filter)
        count_stmt = count_stmt.join(models.Route).where(search_filter)

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


# ---------------------------------------------------------------------------
# Day 10 — Weather-risk evaluation
# ---------------------------------------------------------------------------

_RISK_TO_SEVERITY = {
    RiskLevel.moderate: models.AlertSeverity.medium,
    RiskLevel.high: models.AlertSeverity.high,
}

_RISK_TO_SCORE = {
    RiskLevel.none: 0.1,
    RiskLevel.low: 0.3,
    RiskLevel.moderate: 0.6,
    RiskLevel.high: 0.9,
}


@router.post("/{shipment_id}/evaluate", response_model=schemas.EvaluateResponse)
async def evaluate_shipment(
    shipment_id: int,
    payload: schemas.EvaluateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    weather_service: WeatherService = Depends(get_weather_service),
) -> schemas.EvaluateResponse:
    result = await db.execute(
        select(models.Shipment).where(
            models.Shipment.id == shipment_id,
            models.Shipment.owner_id == current_user.id,
        )
    )
    shipment = result.scalar_one_or_none()

    if shipment is None:
        raise NotFoundError(
            f"Shipment {shipment_id} not found.",
            error_code="shipment_not_found",
        )

    assessment = await weather_service.assess_route_risk(
        payload.latitude,
        payload.longitude,
    )

    if not assessment.available:
        return schemas.EvaluateResponse(
            alerts=[],
            weather_data_available=False,
            note=(
                f"Weather data unavailable ({assessment.reason}); "
                "no weather-based alert was created."
            ),
        )

    if assessment.risk_level not in _RISK_TO_SEVERITY:
        return schemas.EvaluateResponse(
            alerts=[],
            weather_data_available=True,
            weather_risk_level=assessment.risk_level.value,
        )

    alert = models.Alert(
        shipment_id=shipment.id,
        alert_type=models.AlertType.weather_risk,
        severity=_RISK_TO_SEVERITY[assessment.risk_level],
        risk_score=_RISK_TO_SCORE[assessment.risk_level],
        message=(
            f"Weather risk ({assessment.risk_level.value}) detected along route: "
            f"{assessment.precipitation_mm}mm precipitation, "
            f"{assessment.wind_speed_kmh}km/h wind."
        ),
    )

    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    return schemas.EvaluateResponse(
        alerts=[schemas.AlertResponse.model_validate(alert)],
        weather_data_available=True,
        weather_risk_level=assessment.risk_level.value,
    )
