"""PulseFleet — SQL-side search/filter tests (Day 9)."""
import pytest

from tests.conftest import auth_headers, register_and_login, unique_suffix

pytestmark = pytest.mark.asyncio


async def create_shipment(client, token, tracking, weight, origin, destination, priority=1):
    response = await client.post(
        "/shipments",
        json={
            "tracking_number": tracking,
            "weight_kg": weight,
            "priority": priority,
            "scheduled_pickup": "2026-09-10T09:00:00Z",
            "scheduled_delivery": "2026-09-11T09:00:00Z",
            "route": {
                "origin": origin,
                "destination": destination,
                "distance_km": 500,
                "estimated_duration_min": 480,
            },
        },
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_shipment_search_filters_in_sql_and_updates_total(client):
    _, token = await register_and_login(client)
    suffix = unique_suffix()
    await create_shipment(client, token, f"CHENNAI-{suffix}", 100, "Chennai", "Coimbatore")
    await create_shipment(client, token, f"KERALA-{suffix}", 500, "Kochi", "Madurai", priority=2)

    response = await client.get(
        "/shipments",
        params={"search": "Kochi", "min_weight_kg": 400, "max_weight_kg": 600},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["tracking_number"] == f"KERALA-{suffix}"


async def test_shipment_search_empty_result_is_valid_page(client):
    _, token = await register_and_login(client)
    response = await client.get(
        "/shipments",
        params={"search": "definitely-no-such-route"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_shipment_weight_range_rejects_reversed_bounds_with_error_contract(client):
    _, token = await register_and_login(client)
    response = await client.get(
        "/shipments",
        params={"min_weight_kg": 500, "max_weight_kg": 100},
        headers=auth_headers(token),
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "min_weight_kg cannot be greater than max_weight_kg.",
        "error_code": "invalid_weight_range",
    }


async def test_shipment_filter_pagination_remains_correct(client):
    _, token = await register_and_login(client)
    suffix = unique_suffix()
    for weight in (100, 200, 300):
        await create_shipment(client, token, f"PAGE-{weight}-{suffix}", weight, "Chennai", "Salem")

    first = await client.get(
        "/shipments", params={"min_weight_kg": 100, "max_weight_kg": 300, "limit": 2, "offset": 0}, headers=auth_headers(token)
    )
    second = await client.get(
        "/shipments", params={"min_weight_kg": 100, "max_weight_kg": 300, "limit": 2, "offset": 2}, headers=auth_headers(token)
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["total"] >= 3
    assert len(first.json()["items"]) == 2
    assert len(second.json()["items"]) >= 1
    first_ids = {item["id"] for item in first.json()["items"]}
    second_ids = {item["id"] for item in second.json()["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_driver_search_and_status_filter(client):
    _, token = await register_and_login(client)
    suffix = unique_suffix()
    response = await client.post(
        "/drivers",
        json={"name": f"Searchable Ravi {suffix}", "phone": "9000000001", "license_number": f"LIC-{suffix}", "status": "on_leave"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201

    response = await client.get(
        "/drivers", params={"search": suffix, "status": "on_leave"}, headers=auth_headers(token)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["status"] == "on_leave"


async def test_vehicle_search_capacity_range_and_empty_result(client):
    _, token = await register_and_login(client)
    suffix = unique_suffix()
    response = await client.post(
        "/vehicles",
        json={"plate_number": f"TN-{suffix}", "vehicle_type": "truck", "capacity_kg": 5000, "status": "available"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201

    response = await client.get(
        "/vehicles", params={"search": "truck", "min_capacity_kg": 4000, "max_capacity_kg": 6000}, headers=auth_headers(token)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await client.get(
        "/vehicles", params={"search": "no-such-vehicle"}, headers=auth_headers(token)
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_vehicle_invalid_capacity_range_uses_error_contract(client):
    _, token = await register_and_login(client)
    response = await client.get(
        "/vehicles", params={"min_capacity_kg": 5000, "max_capacity_kg": 1000}, headers=auth_headers(token)
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_capacity_range"
