"""
PulseFleet — Reliability tests (Day 11)

Verifies: an unhandled exception never leaks internals to the client (and
carries a request_id for correlation), a failed multi-step write leaves
no partial data behind, and validation error messages are specific
enough to act on (name the field, not just "invalid input").
"""
import pytest

from app.main import app
from app.dependencies import get_current_user
from tests.conftest import auth_headers, register_and_login, unique_suffix

pytestmark = pytest.mark.asyncio


async def test_unhandled_exception_returns_sanitized_500(client):
    """
    Force an unrelated dependency to raise a plain, unexpected exception
    and confirm the client sees only a generic message + request_id —
    never the exception type, message, or a traceback.
    """
    async def broken_dependency():
        raise RuntimeError("simulated unexpected failure — should never reach the client")

    app.dependency_overrides[get_current_user] = broken_dependency
    try:
        resp = await client.get("/shipments")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "internal_error"
    assert "request_id" in body and body["request_id"]
    # The client must never see the actual exception text.
    assert "RuntimeError" not in body["detail"]
    assert "simulated unexpected failure" not in body["detail"]


async def test_every_response_carries_a_request_id_header(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


async def test_validation_error_names_the_offending_field(client):
    """A 422 should be actionable: tell the caller *which* field is wrong, not just 'invalid'."""
    _, token = await register_and_login(client)
    resp = await client.post(
        "/shipments",
        json={
            "tracking_number": f"REL-{unique_suffix()}",
            "weight_kg": -10,  # invalid: must be > 0
            "scheduled_pickup": "2026-09-15T09:00:00Z",
            "scheduled_delivery": "2026-09-16T09:00:00Z",
            "route": {"origin": "A", "destination": "B", "distance_km": 10, "estimated_duration_min": 30},
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "weight_kg" in body["detail"]
    assert body["error_code"] == "validation_error"
    assert "request_id" in body


async def test_failed_shipment_create_leaves_no_partial_data(client):
    """
    A shipment+route create that fails partway (invalid vehicle_id, caught
    before any write) must leave neither a Shipment nor a Route row behind.
    Verified by creating with a bad vehicle_id, then confirming the
    tracking_number is free to reuse afterward — if a partial row had been
    left behind, the retry would 409 on the duplicate tracking_number.
    """
    _, token = await register_and_login(client)
    tracking_number = f"REL-PARTIAL-{unique_suffix()}"
    payload = {
        "tracking_number": tracking_number,
        "vehicle_id": 999999,  # does not exist
        "weight_kg": 50,
        "scheduled_pickup": "2026-09-15T09:00:00Z",
        "scheduled_delivery": "2026-09-16T09:00:00Z",
        "route": {"origin": "A", "destination": "B", "distance_km": 10, "estimated_duration_min": 30},
    }

    resp = await client.post("/shipments", json=payload, headers=auth_headers(token))
    assert resp.status_code == 404  # rejected before any write

    # Retry with the same tracking_number and a valid (absent) vehicle_id —
    # if the first attempt had left a partial row, this would 409.
    payload_retry = dict(payload)
    del payload_retry["vehicle_id"]
    resp2 = await client.post("/shipments", json=payload_retry, headers=auth_headers(token))
    assert resp2.status_code == 201


async def test_failed_driver_create_leaves_no_partial_data(client):
    """Duplicate license_number is rejected without leaving a stray row that would break a later legitimate create."""
    _, token = await register_and_login(client)
    license_number = f"REL-LIC-{unique_suffix()}"

    resp1 = await client.post(
        "/drivers",
        json={"name": "First", "phone": "9000000001", "license_number": license_number},
        headers=auth_headers(token),
    )
    assert resp1.status_code == 201

    # Second attempt with the same license_number correctly 409s...
    resp2 = await client.post(
        "/drivers",
        json={"name": "Second", "phone": "9000000002", "license_number": license_number},
        headers=auth_headers(token),
    )
    assert resp2.status_code == 409

    # ...and did NOT overwrite or duplicate the first driver's data.
    driver_id = resp1.json()["id"]
    resp3 = await client.get(f"/drivers/{driver_id}", headers=auth_headers(token))
    assert resp3.json()["name"] == "First"
