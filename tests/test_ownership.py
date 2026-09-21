"""
PulseFleet — Ownership / authorization tests (Day 8)

Core claim under test: with two independent users A and B, B can never
read, list, update, or delete anything A created — and vice versa.
Every test spins up two fresh users so runs don't interfere with each
other or with any pre-existing data.
"""
import pytest

from tests.conftest import auth_headers, register_and_login, unique_suffix

pytestmark = pytest.mark.asyncio


SHIPMENT_PAYLOAD = {
    "tracking_number": None,  # filled in per-test to stay unique
    "weight_kg": 42.0,
    "scheduled_pickup": "2026-09-10T09:00:00Z",
    "scheduled_delivery": "2026-09-11T09:00:00Z",
    "route": {"origin": "Coimbatore", "destination": "Chennai", "distance_km": 500, "estimated_duration_min": 480},
}


def shipment_payload(tracking_number: str) -> dict:
    payload = dict(SHIPMENT_PAYLOAD)
    payload["tracking_number"] = tracking_number
    return payload


async def create_shipment(client, token, tracking_number):
    resp = await client.post("/shipments", json=shipment_payload(tracking_number), headers=auth_headers(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Shipments — the primary resource
# ---------------------------------------------------------------------------

async def test_owner_can_read_own_shipment(client):
    _, token_a = await register_and_login(client)
    shipment = await create_shipment(client, token_a, f"OWN-READ-{unique_suffix()}")

    resp = await client.get(f"/shipments/{shipment['id']}", headers=auth_headers(token_a))
    assert resp.status_code == 200
    assert resp.json()["id"] == shipment["id"]


async def test_other_user_cannot_read_shipment(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    shipment = await create_shipment(client, token_a, f"CROSS-READ-{unique_suffix()}")

    resp = await client.get(f"/shipments/{shipment['id']}", headers=auth_headers(token_b))
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "shipment_not_found"


async def test_other_user_cannot_update_shipment(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    shipment = await create_shipment(client, token_a, f"CROSS-UPDATE-{unique_suffix()}")

    resp = await client.patch(
        f"/shipments/{shipment['id']}", json={"priority": 3}, headers=auth_headers(token_b)
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "shipment_not_found"

    # Confirm the owner's data was genuinely untouched by the attempt.
    resp = await client.get(f"/shipments/{shipment['id']}", headers=auth_headers(token_a))
    assert resp.json()["priority"] == 1  # unchanged from the default


async def test_other_user_cannot_delete_shipment(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    shipment = await create_shipment(client, token_a, f"CROSS-DELETE-{unique_suffix()}")

    resp = await client.delete(f"/shipments/{shipment['id']}", headers=auth_headers(token_b))
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "shipment_not_found"

    # Still exists for the real owner.
    resp = await client.get(f"/shipments/{shipment['id']}", headers=auth_headers(token_a))
    assert resp.status_code == 200


async def test_list_shipments_is_owner_scoped(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    tn_a1 = f"LIST-SCOPE-A1-{unique_suffix()}"
    tn_a2 = f"LIST-SCOPE-A2-{unique_suffix()}"
    tn_b1 = f"LIST-SCOPE-B1-{unique_suffix()}"
    await create_shipment(client, token_a, tn_a1)
    await create_shipment(client, token_a, tn_a2)
    await create_shipment(client, token_b, tn_b1)

    resp = await client.get("/shipments", params={"limit": 100}, headers=auth_headers(token_a))
    assert resp.status_code == 200
    body = resp.json()
    tracking_numbers = {item["tracking_number"] for item in body["items"]}
    assert tn_a1 in tracking_numbers
    assert tn_a2 in tracking_numbers
    assert tn_b1 not in tracking_numbers  # B's shipment must never leak into A's list

    resp_b = await client.get("/shipments", params={"limit": 100}, headers=auth_headers(token_b))
    tracking_numbers_b = {item["tracking_number"] for item in resp_b.json()["items"]}
    assert tn_b1 in tracking_numbers_b
    assert tn_a1 not in tracking_numbers_b
    assert tn_a2 not in tracking_numbers_b


async def test_cannot_assign_another_users_driver_to_own_shipment(client):
    """
    A user shouldn't be able to attach someone else's driver to their own
    shipment, even though driver_id is just an integer they could guess.
    """
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)

    resp = await client.post(
        "/drivers",
        json={"name": "A's Driver", "phone": "9000000000", "license_number": f"LIC-{unique_suffix()}"},
        headers=auth_headers(token_a),
    )
    assert resp.status_code == 201
    driver_a_id = resp.json()["id"]

    # B tries to create a shipment using A's driver id.
    payload = shipment_payload(f"CROSS-DRIVER-{unique_suffix()}")
    payload["driver_id"] = driver_a_id
    resp = await client.post("/shipments", json=payload, headers=auth_headers(token_b))
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "driver_not_found"


# ---------------------------------------------------------------------------
# Drivers and vehicles — same ownership rule applies
# ---------------------------------------------------------------------------

async def test_other_user_cannot_read_or_modify_driver(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)

    resp = await client.post(
        "/drivers",
        json={"name": "Ravi", "phone": "9111111111", "license_number": f"LIC-DRV-{unique_suffix()}"},
        headers=auth_headers(token_a),
    )
    driver_id = resp.json()["id"]

    resp = await client.get(f"/drivers/{driver_id}", headers=auth_headers(token_b))
    assert resp.status_code == 404

    resp = await client.patch(f"/drivers/{driver_id}", json={"phone": "9999999999"}, headers=auth_headers(token_b))
    assert resp.status_code == 404

    resp = await client.delete(f"/drivers/{driver_id}", headers=auth_headers(token_b))
    assert resp.status_code == 404

    # Owner still sees it, untouched.
    resp = await client.get(f"/drivers/{driver_id}", headers=auth_headers(token_a))
    assert resp.status_code == 200
    assert resp.json()["phone"] == "9111111111"


async def test_other_user_cannot_read_or_modify_vehicle(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)

    resp = await client.post(
        "/vehicles",
        json={"plate_number": f"PLT-{unique_suffix()}", "vehicle_type": "truck", "capacity_kg": 1000},
        headers=auth_headers(token_a),
    )
    vehicle_id = resp.json()["id"]

    resp = await client.get(f"/vehicles/{vehicle_id}", headers=auth_headers(token_b))
    assert resp.status_code == 404

    resp = await client.patch(
        f"/vehicles/{vehicle_id}", json={"capacity_kg": 5000}, headers=auth_headers(token_b)
    )
    assert resp.status_code == 404

    resp = await client.delete(f"/vehicles/{vehicle_id}", headers=auth_headers(token_b))
    assert resp.status_code == 404


async def test_list_drivers_and_vehicles_is_owner_scoped(client):
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    plate_number = f"SCOPE-{unique_suffix()}"

    await client.post(
        "/vehicles",
        json={"plate_number": plate_number, "vehicle_type": "van", "capacity_kg": 500},
        headers=auth_headers(token_b),
    )

    resp = await client.get("/vehicles", params={"limit": 100}, headers=auth_headers(token_a))
    plates = {v["plate_number"] for v in resp.json()["items"]}
    assert plate_number not in plates
