"""
PulseFleet — Pydantic request/response models
Day 2: API design
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Enums (shared vocab across entities)
# ---------------------------------------------------------------------------

class ShipmentStatus(str, Enum):
    pending = "pending"
    in_transit = "in_transit"
    delivered = "delivered"
    cancelled = "cancelled"


class DriverStatus(str, Enum):
    active = "active"
    on_leave = "on_leave"
    off_duty = "off_duty"


class VehicleStatus(str, Enum):
    available = "available"
    in_use = "in_use"
    maintenance = "maintenance"


class AlertType(str, Enum):
    predicted_delay = "predicted_delay"
    weather_risk = "weather_risk"
    capacity_overload = "capacity_overload"
    route_blocked = "route_blocked"


class AlertSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class DriverBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=7, max_length=15)
    license_number: str = Field(..., min_length=3, max_length=30)
    status: DriverStatus = DriverStatus.active


class DriverCreate(DriverBase):
    pass


class DriverUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    license_number: Optional[str] = None
    status: Optional[DriverStatus] = None


class DriverResponse(DriverBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active_shipment_count: int = 0
    created_at: datetime


# ---------------------------------------------------------------------------
# Vehicle
# ---------------------------------------------------------------------------

class VehicleBase(BaseModel):
    plate_number: str = Field(..., min_length=2, max_length=20)
    vehicle_type: str = Field(..., examples=["truck", "van", "bike"])
    capacity_kg: float = Field(..., gt=0)
    status: VehicleStatus = VehicleStatus.available


class VehicleCreate(VehicleBase):
    pass


class VehicleUpdate(BaseModel):
    plate_number: Optional[str] = None
    vehicle_type: Optional[str] = None
    capacity_kg: Optional[float] = Field(default=None, gt=0)
    status: Optional[VehicleStatus] = None


class VehicleResponse(VehicleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Route (attached 1:1 to a shipment; holds risk signals used for prediction)
# ---------------------------------------------------------------------------

class RouteBase(BaseModel):
    origin: str = Field(..., min_length=1, max_length=150)
    destination: str = Field(..., min_length=1, max_length=150)
    distance_km: float = Field(..., gt=0)
    estimated_duration_min: int = Field(..., gt=0)
    weather_risk_flag: bool = False
    traffic_risk_flag: bool = False


class RouteCreate(RouteBase):
    pass


class RouteResponse(RouteBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int


# ---------------------------------------------------------------------------
# Shipment (core entity)
# ---------------------------------------------------------------------------

class ShipmentBase(BaseModel):
    tracking_number: str = Field(..., min_length=4, max_length=40)
    driver_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    weight_kg: float = Field(..., gt=0)
    priority: int = Field(default=1, ge=1, le=3, description="1=standard, 2=express, 3=urgent")
    scheduled_pickup: datetime
    scheduled_delivery: datetime


class ShipmentCreate(ShipmentBase):
    route: RouteCreate


class ShipmentUpdate(BaseModel):
    driver_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    status: Optional[ShipmentStatus] = None
    actual_pickup: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    priority: Optional[int] = Field(default=None, ge=1, le=3)


class ShipmentResponse(ShipmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: ShipmentStatus
    actual_pickup: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    route: RouteResponse
    created_at: datetime


# ---------------------------------------------------------------------------
# Alert (predictive exception output — generated, not directly created by users)
# ---------------------------------------------------------------------------

class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shipment_id: int
    alert_type: AlertType
    severity: AlertSeverity
    risk_score: float = Field(..., ge=0, le=1)
    message: str
    resolved: bool = False
    created_at: datetime


class AlertResolveRequest(BaseModel):
    resolution_note: Optional[str] = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Shared error contract
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
