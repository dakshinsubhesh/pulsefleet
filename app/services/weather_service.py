"""
PulseFleet — Weather service integration (Day 10: Service integration)

Wraps the Open-Meteo forecast API (https://open-meteo.com — free, no API
key required) behind a small, focused interface. This is the ONLY file
in the codebase that knows the external API's URL, request shape, or
response shape — route handlers never touch httpx or the API contract
directly, they just call `WeatherService.assess_route_risk(lat, lon)`
and get back a plain `WeatherAssessment`.

Failure handling philosophy: weather data is an input to a *prediction*,
not a hard dependency the whole request should die over. If the service
times out, errors, or returns something unparseable, this returns a
`WeatherAssessment(available=False, ...)` rather than raising — callers
decide what "unavailable" means for them (here: skip creating a
weather-based alert, but the rest of the request still succeeds).
"""
import os
from dataclasses import dataclass
from enum import Enum

import httpx


class RiskLevel(str, Enum):
    none = "none"
    low = "low"
    moderate = "moderate"
    high = "high"


@dataclass
class WeatherAssessment:
    available: bool
    risk_level: RiskLevel = RiskLevel.none
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    reason: str | None = None  # set when available=False: "timeout", "http_error", "malformed_response"


def _classify_risk(precipitation_mm: float, wind_speed_kmh: float) -> RiskLevel:
    """
    Simple, explainable thresholds — not a ML model, just the kind of
    rule a logistics dispatcher would use: heavy rain or high wind means
    real delay risk; light rain or breeze is background noise.
    """
    if precipitation_mm >= 10 or wind_speed_kmh >= 40:
        return RiskLevel.high
    if precipitation_mm >= 2 or wind_speed_kmh >= 25:
        return RiskLevel.moderate
    if precipitation_mm > 0 or wind_speed_kmh >= 15:
        return RiskLevel.low
    return RiskLevel.none


class WeatherService:
    """
    Thin, timeout-bounded, mockable wrapper around an external weather API.

    - `base_url` and `timeout_seconds` are read from the environment by
      default (never hardcoded), but can be overridden — the latter is
      what makes this trivially mockable in tests (see
      tests/test_weather_service.py): pass in an httpx.AsyncClient built
      with a MockTransport instead of hitting the network.
    - One retry on timeout/connection error before giving up, since
      transient network blips are common and not worth failing a whole
      shipment-evaluation request over.
    """

    def __init__(self, client: httpx.AsyncClient | None = None, max_retries: int = 1) -> None:
        base_url = os.getenv("WEATHER_API_BASE_URL", "https://api.open-meteo.com/v1/forecast")
        timeout_seconds = float(os.getenv("WEATHER_API_TIMEOUT_SECONDS", "5"))

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._base_url = base_url
        self.max_retries = max_retries

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def assess_route_risk(self, latitude: float, longitude: float) -> WeatherAssessment:
        """
        Fetches current precipitation/wind for a point on the route and
        classifies it into a RiskLevel. Never raises — failures come back
        as WeatherAssessment(available=False, reason=...).
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "precipitation,wind_speed_10m",
        }

        last_error_reason = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = await self._client.get(self._base_url, params=params)
                response.raise_for_status()
                data = response.json()
                current = data["current"]
                precipitation_mm = float(current["precipitation"])
                wind_speed_kmh = float(current["wind_speed_10m"])
                return WeatherAssessment(
                    available=True,
                    risk_level=_classify_risk(precipitation_mm, wind_speed_kmh),
                    precipitation_mm=precipitation_mm,
                    wind_speed_kmh=wind_speed_kmh,
                )
            except httpx.TimeoutException:
                last_error_reason = "timeout"
            except httpx.HTTPStatusError:
                last_error_reason = "http_error"
            except httpx.HTTPError:
                last_error_reason = "connection_error"
            except (KeyError, TypeError, ValueError):
                # Response came back 200 but wasn't shaped the way we expect —
                # don't retry (the API isn't going to reshape itself), just
                # fail safe.
                return WeatherAssessment(available=False, reason="malformed_response")

        return WeatherAssessment(available=False, reason=last_error_reason)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
# A module-level singleton so the underlying httpx connection pool is
# reused across requests instead of opening a new one per call. Tests
# override this dependency (app.dependency_overrides[get_weather_service])
# to inject a WeatherService built on a mocked transport — the route code
# is never changed to make that possible.
_weather_service_singleton: WeatherService | None = None


def get_weather_service() -> WeatherService:
    global _weather_service_singleton
    if _weather_service_singleton is None:
        _weather_service_singleton = WeatherService()
    return _weather_service_singleton
