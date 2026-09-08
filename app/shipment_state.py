"""
PulseFleet — Shipment status transition rules (Day 6: Update and delete)

Keeps the state machine in one place so both the update endpoint and any
future rules-engine code (Day 9-10) agree on what a "valid" shipment
lifecycle looks like.
"""
from app.models import ShipmentStatus

# pending -> in_transit -> delivered
# pending / in_transit -> cancelled
# delivered / cancelled are terminal: no further transitions.
ALLOWED_TRANSITIONS: dict[ShipmentStatus, set[ShipmentStatus]] = {
    ShipmentStatus.pending: {ShipmentStatus.in_transit, ShipmentStatus.cancelled},
    ShipmentStatus.in_transit: {ShipmentStatus.delivered, ShipmentStatus.cancelled},
    ShipmentStatus.delivered: set(),
    ShipmentStatus.cancelled: set(),
}


def is_valid_transition(current: ShipmentStatus, new: ShipmentStatus) -> bool:
    if current == new:
        return True  # no-op update (e.g. re-sending the same status) is harmless
    return new in ALLOWED_TRANSITIONS.get(current, set())


def is_terminal(status: ShipmentStatus) -> bool:
    return status in (ShipmentStatus.delivered, ShipmentStatus.cancelled)
