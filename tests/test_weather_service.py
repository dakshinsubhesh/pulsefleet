"""
PulseFleet — WeatherService unit tests (Day 10: Service integration)

No real network call is ever made here — httpx's transport is mocked
with `respx`, which intercepts requests at the transport layer. This is
the point of putting the integration behind WeatherService: the service
boundary is exactly what makes it possible to simulate success, slow
responses, outages, and garbage responses without a live dependency.
"""
import httpx
import pytest
import respx

from app.services.weather_service import RiskLevel, WeatherService

pytestmark = pytest.mark.asyncio

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def make_service() -> WeatherService:
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=1.0)
    return WeatherService(client=client, max_retries=1)


@respx.mock
async def test_successful_low_risk_response():
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"current": {"precipitation": 0.0, "wind_speed_10m": 5.0}})
    )
    service = make_service()
    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is True
    assert result.risk_level == RiskLevel.none
    assert result.precipitation_mm == 0.0


@respx.mock
async def test_successful_high_risk_response():
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"current": {"precipitation": 15.0, "wind_speed_10m": 50.0}})
    )
    service = make_service()
    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is True
    assert result.risk_level == RiskLevel.high
    assert result.precipitation_mm == 15.0
    assert result.wind_speed_kmh == 50.0


@respx.mock
async def test_moderate_risk_from_wind_alone():
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"current": {"precipitation": 0.0, "wind_speed_10m": 30.0}})
    )
    service = make_service()
    result = await service.assess_route_risk(11.0, 77.0)
    assert result.risk_level == RiskLevel.moderate


@respx.mock
async def test_timeout_is_handled_gracefully():
    """A hung upstream must not raise — it must come back as unavailable."""
    respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("connect timed out"))
    service = make_service()

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is False
    assert result.risk_level == RiskLevel.none
    assert result.reason == "timeout"


@respx.mock
async def test_http_500_is_handled_gracefully():
    respx.get(BASE_URL).mock(return_value=httpx.Response(500, text="internal server error"))
    service = make_service()

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is False
    assert result.reason == "http_error"


@respx.mock
async def test_connection_error_is_handled_gracefully():
    respx.get(BASE_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    service = make_service()

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is False
    assert result.reason == "connection_error"


@respx.mock
async def test_malformed_response_is_handled_gracefully():
    """A 200 with an unexpected shape (API contract drift) must not raise or retry forever."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    service = make_service()

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is False
    assert result.reason == "malformed_response"


@respx.mock
async def test_retries_once_then_succeeds():
    """First call times out, second (the retry) succeeds — proves the retry path works."""
    route = respx.get(BASE_URL)
    route.side_effect = [
        httpx.TimeoutException("connect timed out"),
        httpx.Response(200, json={"current": {"precipitation": 1.0, "wind_speed_10m": 10.0}}),
    ]
    service = make_service()

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is True
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_exhausting_retries():
    """Every attempt fails -> still returns cleanly as unavailable, doesn't retry forever."""
    respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("connect timed out"))
    service = WeatherService(client=httpx.AsyncClient(base_url=BASE_URL, timeout=1.0), max_retries=2)

    result = await service.assess_route_risk(11.0, 77.0)

    assert result.available is False
    assert result.reason == "timeout"
