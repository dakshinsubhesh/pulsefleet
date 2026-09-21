# PulseFleet API Documentation

Predictive exception & delay alert system for fleet/shipment operations.

Interactive docs (auto-generated from the code, always in sync): once the app is running,
visit `/docs` (Swagger UI — supports trying requests with the **Authorize** button) or
`/redoc` (read-only, nicer for long-form reading).

This document covers what the auto-generated docs don't: the auth flow end to end, a
plain-language endpoint reference, the full error-code table, and worked `curl` examples
for the main workflow.

---

## 1. Authentication

Every endpoint except `/`, `/health`, `/auth/register`, and `/auth/login` requires a
Bearer token. All resources (shipments, drivers, vehicles) are **owner-scoped** — you
only ever see and modify what your own account created (see [Ownership](#5-ownership--authorization)).

### Register

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "dakshin", "password": "SuperSecret123"}'
```

`password` must be 8–72 characters (72 is bcrypt's hashing limit).

### Log in

Uses the standard OAuth2 password flow (form-encoded body, not JSON) — this is what lets
Swagger's **Authorize** button work without any extra glue code.

```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=dakshin&password=SuperSecret123"
```

Response:

```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in_minutes": 60 }
```

### Use the token

Every subsequent request needs this header:

```
Authorization: Bearer <access_token>
```

```bash
curl http://localhost:8000/auth/me -H "Authorization: Bearer eyJ..."
```

Tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60, set in `.env`). An expired
or malformed token gets a `401` with `error_code: token_expired` or `invalid_token` — see
the [error table](#6-error-reference) below.

---

## 2. Endpoint reference

**Auth**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | — | Create an account |
| POST | `/auth/login` | — | Exchange username/password for a token |
| GET | `/auth/me` | required | The caller's own profile |

**Shipments** — the core resource

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/shipments` | required | Create a shipment + its route in one transaction |
| GET | `/shipments` | required | List (paginated, filterable — see [§4](#4-filtering-search-and-pagination)) |
| GET | `/shipments/{id}` | required | Fetch one shipment |
| PATCH | `/shipments/{id}` | required | Partial update — status changes follow the lifecycle in [§3](#3-shipment-lifecycle) |
| DELETE | `/shipments/{id}` | required | Only while `status=pending`; cascades to its route |
| POST | `/shipments/{id}/evaluate` | required | Checks weather at a lat/lon and creates a `weather_risk` alert if conditions are moderate+ ([Day 10](../README.md#day-10-service-integration)) |

**Drivers** and **Vehicles** — same shape as each other

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/drivers` \| `/vehicles` | required | Create |
| GET | `/drivers` \| `/vehicles` | required | List (paginated, filterable) |
| GET | `/drivers/{id}` \| `/vehicles/{id}` | required | Fetch one |
| PATCH | `/drivers/{id}` \| `/vehicles/{id}` | required | Partial update |
| DELETE | `/drivers/{id}` \| `/vehicles/{id}` | required | Blocked (`409`) while the driver/vehicle has an active (`pending`/`in_transit`) shipment |

**System**

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Service banner |
| GET | `/health` | — | Liveness probe: status, version, UTC timestamp |

---

## 3. Shipment lifecycle

A shipment's `status` only moves forward through this state machine (`app/shipment_state.py`):

```
pending ──▶ in_transit ──▶ delivered
   │             │
   └─────────────┴──▶ cancelled
```

`delivered` and `cancelled` are **terminal** — once reached, the shipment is closed to
*any* further edit (not just status changes), and `DELETE` only works while `status`
is still `pending` (delete a mistake before it starts; `cancel` anything already moving).

---

## 4. Filtering, search, and pagination

All list endpoints (`GET /shipments`, `/drivers`, `/vehicles`) return a `Page` envelope:

```json
{ "items": [...], "total": 42, "limit": 20, "offset": 0 }
```

`limit` (1–100, default 20) and `offset` (≥0) are always available. Every filter below
runs as SQL (`WHERE`), never as an in-memory filter over the full table — see
[Day 9](../README.md#day-9-search-and-filters).

| Resource | Filters |
|---|---|
| Shipments | `q` (tracking_number, ILIKE), `origin`/`destination` (route, ILIKE), `status`, `driver_id`, `vehicle_id`, `priority`, `weight_min`/`weight_max` |
| Drivers | `q` (name/license_number/phone, ILIKE), `status` |
| Vehicles | `q` (plate_number/vehicle_type, ILIKE), `status`, `vehicle_type`, `capacity_min`/`capacity_max` |

A filter combination matching nothing returns `200` with an empty `items` array —
never a `404`.

```bash
curl "http://localhost:8000/shipments?status=pending&priority=3&limit=10" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 5. Ownership & authorization

Every record belongs to exactly one user (`owner_id`). Reading, updating, or deleting a
record that belongs to someone else returns the **same `404`** used for a record that
doesn't exist at all — never a `403`. This is deliberate: a `403` would confirm the id is
real and just off-limits, letting someone enumerate ids belonging to other accounts.
See [Day 8](../README.md#day-8-authorization).

This also applies to references: you cannot attach another user's `driver_id` or
`vehicle_id` to your own shipment, even if you know or guess its numeric id — you'll get
a `404` on that field instead.

---

## 6. Error reference

Every error response has the same shape:

```json
{ "detail": "human-readable, actionable message", "error_code": "machine_readable_code", "request_id": "uuid" }
```

`request_id` correlates the response to a server-side log line — include it when
reporting an issue (see [Day 11](../README.md#day-11-reliability)).

| HTTP | `error_code` | Meaning |
|---|---|---|
| 401 | `not_authenticated` | No Bearer token was supplied |
| 401 | `invalid_token` | Token signature invalid, or the user no longer exists/is inactive |
| 401 | `token_expired` | Token's expiry has passed — log in again |
| 401 | `invalid_credentials` | Wrong username or password at `/auth/login` |
| 401 | `inactive_user` | Account exists but is deactivated |
| 404 | `shipment_not_found` / `driver_not_found` / `vehicle_not_found` | Doesn't exist, or belongs to another user |
| 409 | `duplicate_username` | Username already registered |
| 409 | `duplicate_tracking_number` / `duplicate_license_number` / `duplicate_plate_number` | That unique field is already in use |
| 409 | `shipment_terminal_state` | Shipment is `delivered`/`cancelled`; no further edits allowed |
| 409 | `invalid_status_transition` | Requested status isn't reachable from the current one (see [§3](#3-shipment-lifecycle)) |
| 409 | `shipment_not_deletable` | Shipment isn't `pending`; cancel it via `PATCH` instead |
| 409 | `driver_has_active_shipments` / `vehicle_has_active_shipments` | Can't delete while it has a `pending`/`in_transit` shipment |
| 409 | `shipment_write_conflict` / `driver_write_conflict` / `vehicle_write_conflict` | A database constraint was violated at write time |
| 422 | `validation_error` | A field failed validation — `detail` names which field and why |
| 422 | `invalid_range` | A `*_min` query param is greater than its `*_max` counterpart |
| 500 | `internal_error` | Unexpected server failure — the client never sees details, only `request_id`; full detail is in the server's structured logs |

---

## 7. Worked example: full shipment lifecycle

```bash
BASE=http://localhost:8000

# Register + log in
curl -s -X POST $BASE/auth/register -H "Content-Type: application/json" \
  -d '{"username":"dakshin","password":"SuperSecret123"}' > /dev/null
TOKEN=$(curl -s -X POST $BASE/auth/login -d "username=dakshin&password=SuperSecret123" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

# Create a driver and a vehicle
DRIVER_ID=$(curl -s -X POST $BASE/drivers -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name":"Ravi Kumar","phone":"9876543210","license_number":"TN-DL-0001"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
VEHICLE_ID=$(curl -s -X POST $BASE/vehicles -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"plate_number":"TN-01-AB-1234","vehicle_type":"truck","capacity_kg":2000}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

# Create a shipment
SHIPMENT_ID=$(curl -s -X POST $BASE/shipments -H "$AUTH" -H "Content-Type: application/json" -d '{
  "tracking_number": "PF-1001", "driver_id": '"$DRIVER_ID"', "vehicle_id": '"$VEHICLE_ID"',
  "weight_kg": 450.5, "priority": 2,
  "scheduled_pickup": "2026-09-20T09:00:00Z", "scheduled_delivery": "2026-09-21T18:00:00Z",
  "route": {"origin": "Coimbatore", "destination": "Chennai", "distance_km": 500, "estimated_duration_min": 480}
}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

# Move it along its lifecycle
curl -s -X PATCH $BASE/shipments/$SHIPMENT_ID -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status":"in_transit"}' > /dev/null

# Check weather risk along the route
curl -s -X POST $BASE/shipments/$SHIPMENT_ID/evaluate -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"latitude": 11.0, "longitude": 77.0}'

curl -s -X PATCH $BASE/shipments/$SHIPMENT_ID -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status":"delivered"}' > /dev/null
```

---

## 8. Running the tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

Tests run against a dedicated database (`pulsefleet_test_db` by default, override with
`TEST_DATABASE_URL`) — never the development database. See
[Day 12](../README.md#day-12-automated-tests).
