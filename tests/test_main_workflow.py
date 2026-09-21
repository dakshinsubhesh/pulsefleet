"""
PulseFleet — Main workflow test (Day 12: Automated tests)

While the full journey is exercised piecemeal across the other test
files, this one test walks the complete happy path a real user would
take, start to finish, in a single readable sequence: register -> login
-> create driver -> create vehicle -> create shipment -> list -> get
detail -> update -> evaluate (weather check, mocked) -> delete.

If this one test is broken, the product's core promise is broken —
that's the point of having it as a single, explicit "main workflow"
test rather than only inferring coverage from scattered unit tests.
"""
import pytest

from app.main import app
from app.services.weather_service import RiskLevel, WeatherAssessment, get_weather_service
from tests.conftest import auth_headers, register_and_login, unique_suffix

pytestmark = pytest.mark.asyncio


async def test_full_shipment_lifecycle_happy_path(client):
    # 1. Register + login
    username, token = await register_and_login(client)
    headers = auth_headers(token)
    suffix = unique_suffix()

    # 2. Create a driver
    resp = await client.post(
        "/drivers",
        json={"name": "Ravi Kumar", "phone": "9876543210", "license_number": f"TN-DL-{suffix}"},
        headers=headers,
    )
    assert resp.status_code == 201
    driver_id = resp.json()["id"]

    # 3. Create a vehicle
    resp = await client.post(
        "/vehicles",
        json={"plate_number": f"TN-01-{suffix}", "vehicle_type": "truck", "capacity_kg": 2000},
        headers=headers,
    )
    assert resp.status_code == 201
    vehicle_id = resp.json()["id"]

    # 4. Create a shipment linking both, with its route
    tracking_number = f"MAIN-{suffix}"
    resp = await client.post(
        "/shipments",
        json={
            "tracking_number": tracking_number,
            "driver_id": driver_id,
            "vehicle_id": vehicle_id,
            "weight_kg": 450.5,
            "priority": 2,
            "scheduled_pickup": "2026-09-20T09:00:00Z",
            "scheduled_delivery": "2026-09-21T18:00:00Z",
            "route": {
                "origin": "Coimbatore", "destination": "Chennai",
                "distance_km": 500, "estimated_duration_min": 480,
            },
        },
        headers=headers,
    )
    assert resp.status_code == 201
    shipment = resp.json()
    shipment_id = shipment["id"]
    assert shipment["status"] == "pending"
    assert shipment["route"]["origin"] == "Coimbatore"

    # 5. It shows up in the list
    resp = await client.get("/shipments", params={"q": tracking_number}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # 6. Fetch the detail directly
    resp = await client.get(f"/shipments/{shipment_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["tracking_number"] == tracking_number

    # 7. Move it through its lifecycle: pending -> in_transit
    resp = await client.patch(f"/shipments/{shipment_id}", json={"status": "in_transit"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_transit"

    # 8. Evaluate weather risk along the route (mocked — no real network call)
    app.dependency_overrides[get_weather_service] = lambda: _FakeWeatherService(
        WeatherAssessment(available=True, risk_level=RiskLevel.high, precipitation_mm=18.0, wind_speed_kmh=45.0)
    )
    try:
        resp = await client.post(
            f"/shipments/{shipment_id}/evaluate",
            json={"latitude": 11.0, "longitude": 77.0},
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_weather_service, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather_data_available"] is True
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["alert_type"] == "weather_risk"

    # 9. Finish the lifecycle: in_transit -> delivered
    resp = await client.patch(f"/shipments/{shipment_id}", json={"status": "delivered"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"

    # 10. Delivered shipments are closed to further edits (business rule from Day 6)
    resp = await client.patch(f"/shipments/{shipment_id}", json={"priority": 1}, headers=headers)
    assert resp.status_code == 409

    # 11. Clean up the driver/vehicle now that the shipment is resolved
    resp = await client.delete(f"/drivers/{driver_id}", headers=headers)
    assert resp.status_code == 204
    resp = await client.delete(f"/vehicles/{vehicle_id}", headers=headers)
    assert resp.status_code == 204


class _FakeWeatherService:
    def __init__(self, assessment: WeatherAssessment):
        self._assessment = assessment

    async def assess_route_risk(self, latitude, longitude) -> WeatherAssessment:
        return self._assessment
