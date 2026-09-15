"""
PulseFleet — /shipments/{id}/evaluate endpoint tests (Day 10)

These tests never touch WeatherService's internals or httpx at all —
they override the `get_weather_service` FastAPI dependency with a fake
that returns canned WeatherAssessment values. This is the other half of
"integration can be mocked in tests": the route itself is testable
without knowing anything about the weather API's transport, precisely
because it only depends on the WeatherService interface.
"""
import pytest

from app.main import app
from app.services.weather_service import RiskLevel, WeatherAssessment, get_weather_service
from tests.conftest import auth_headers, register_and_login, unique_suffix

pytestmark = pytest.mark.asyncio


class FakeWeatherService:
    def __init__(self, assessment: WeatherAssessment):
        self._assessment = assessment

    async def assess_route_risk(self, latitude, longitude) -> WeatherAssessment:
        return self._assessment


def override_weather_service(assessment: WeatherAssessment):
    app.dependency_overrides[get_weather_service] = lambda: FakeWeatherService(assessment)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.pop(get_weather_service, None)


async def create_shipment(client, token, tracking_number):
    payload = {
        "tracking_number": tracking_number,
        "weight_kg": 100.0,
        "scheduled_pickup": "2026-09-15T09:00:00Z",
        "scheduled_delivery": "2026-09-16T09:00:00Z",
        "route": {"origin": "Coimbatore", "destination": "Chennai", "distance_km": 500, "estimated_duration_min": 480},
    }
    resp = await client.post("/shipments", json=payload, headers=auth_headers(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_evaluate_creates_alert_on_high_risk(client):
    override_weather_service(
        WeatherAssessment(available=True, risk_level=RiskLevel.high, precipitation_mm=20.0, wind_speed_kmh=55.0)
    )
    _, token = await register_and_login(client)
    shipment = await create_shipment(client, token, f"EVAL-HIGH-{unique_suffix()}")

    resp = await client.post(
        f"/shipments/{shipment['id']}/evaluate",
        json={"latitude": 11.0, "longitude": 77.0},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather_data_available"] is True
    assert body["weather_risk_level"] == "high"
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["alert_type"] == "weather_risk"
    assert body["alerts"][0]["severity"] == "high"


async def test_evaluate_creates_no_alert_on_low_risk(client):
    override_weather_service(WeatherAssessment(available=True, risk_level=RiskLevel.none))
    _, token = await register_and_login(client)
    shipment = await create_shipment(client, token, f"EVAL-LOW-{unique_suffix()}")

    resp = await client.post(
        f"/shipments/{shipment['id']}/evaluate",
        json={"latitude": 11.0, "longitude": 77.0},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather_data_available"] is True
    assert body["alerts"] == []


async def test_evaluate_handles_unavailable_weather_service_gracefully(client):
    """
    The whole point of the failure-handling design: when the weather
    service is down, the endpoint must still return 200 — it just can't
    produce a weather-based alert. It must NOT 500 or bubble up an
    httpx exception.
    """
    override_weather_service(WeatherAssessment(available=False, reason="timeout"))
    _, token = await register_and_login(client)
    shipment = await create_shipment(client, token, f"EVAL-DOWN-{unique_suffix()}")

    resp = await client.post(
        f"/shipments/{shipment['id']}/evaluate",
        json={"latitude": 11.0, "longitude": 77.0},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["weather_data_available"] is False
    assert body["alerts"] == []
    assert "timeout" in body["note"]


async def test_evaluate_is_owner_scoped(client):
    """Consistent with Day 8: can't evaluate someone else's shipment."""
    override_weather_service(WeatherAssessment(available=True, risk_level=RiskLevel.high))
    _, token_a = await register_and_login(client)
    _, token_b = await register_and_login(client)
    shipment = await create_shipment(client, token_a, f"EVAL-CROSS-{unique_suffix()}")

    resp = await client.post(
        f"/shipments/{shipment['id']}/evaluate",
        json={"latitude": 11.0, "longitude": 77.0},
        headers=auth_headers(token_b),
    )
    assert resp.status_code == 404


async def test_evaluate_nonexistent_shipment_404(client):
    override_weather_service(WeatherAssessment(available=True, risk_level=RiskLevel.none))
    _, token = await register_and_login(client)

    resp = await client.post(
        "/shipments/9999999/evaluate",
        json={"latitude": 11.0, "longitude": 77.0},
        headers=auth_headers(token),
    )
    assert resp.status_code == 404


async def test_evaluate_requires_auth(client):
    resp = await client.post("/shipments/1/evaluate", json={"latitude": 11.0, "longitude": 77.0})
    assert resp.status_code == 401


async def test_evaluate_validates_coordinates(client):
    _, token = await register_and_login(client)
    shipment = await create_shipment(client, token, f"EVAL-BADCOORD-{unique_suffix()}")

    resp = await client.post(
        f"/shipments/{shipment['id']}/evaluate",
        json={"latitude": 999, "longitude": 77.0},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422
