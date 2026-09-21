# PulseFleet — Predictive Exception & Delay Alert System

15-day Python backend development track — Transport / Logistics domain.

A FastAPI + PostgreSQL backend for fleet/shipment operations: owner-scoped CRUD for
drivers, vehicles, and shipments; JWT authentication and authorization; SQL-level
search and filtering; a weather-risk service integration with graceful degradation;
structured logging and reliable error handling; and a 41-test automated suite running
against an isolated test database.

## Quick start

**Prerequisites:** Python 3.12+, PostgreSQL 16+

```bash
# 1. Create the database, role, and a separate test database
psql -c "CREATE USER pulsefleet WITH PASSWORD 'changeme';"
psql -c "CREATE DATABASE pulsefleet_db OWNER pulsefleet;"
psql -c "CREATE DATABASE pulsefleet_test_db OWNER pulsefleet;"

# 2. Configure environment
cp .env.example .env   # edit DATABASE_URL / SECRET_KEY to match your setup

# 3. Install
python3 -m venv venv
source venv/bin/activate      # Windows: venv\\Scripts\\activate
pip install -r requirements.txt -r requirements-dev.txt

# 4. Migrate
alembic upgrade head

# 5. Run
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000 — interactive docs at `/docs`, `/redoc`
- Full API reference, auth flow, and error codes: [`docs/API.md`](docs/API.md)
- Production deployment checklist (env vars, health checks, Docker, startup command): [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- Design rationale (entities, endpoint plan): [`docs/api-design.md`](docs/api-design.md)
- Run the tests (isolated database, never touches dev data): `pytest tests/ -v`
- A full worked demo of the core flow: see [Day 15: Final Review](#day-15-final-review) below

---

## Day 1: Foundation and environment

Sets up the FastAPI project skeleton, virtual environment, and a `/health` endpoint.

### Setup

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Run

```bash
uvicorn app.main:app --reload
```

### Verify

- Health check: http://127.0.0.1:8000/health
- Interactive docs: http://127.0.0.1:8000/docs

## Day 3: Database setup

PostgreSQL + async SQLAlchemy + Alembic.

### Setup

1. Create the DB and a role (adjust credentials as needed):
   ```sql
   CREATE USER pulsefleet WITH PASSWORD 'changeme';
   CREATE DATABASE pulsefleet_db OWNER pulsefleet;
   ```
2. Copy `.env.example` to `.env` and set `DATABASE_URL` to match.
3. Install deps: `pip install -r requirements.txt`

### Run migrations

```bash
alembic upgrade head       # apply all migrations
alembic downgrade base     # roll back to empty schema
alembic current            # show current revision
alembic revision --autogenerate -m "message"   # generate a new migration after model changes
```

Verified: `upgrade head` → `downgrade base` → `upgrade head` runs cleanly with no leftover objects (Postgres ENUM types are explicitly dropped in `downgrade()`, since Alembic's autogenerate doesn't do this by default).

### Core tables

`drivers`, `vehicles`, `shipments`, `routes` (1:1 with shipment), `alerts` (many per shipment) — see `app/models.py` and `docs/api-design.md` for the full entity relationships.

## Day 4: Create workflow

Implemented `POST /shipments` — the primary create endpoint — plus `POST /drivers` and `POST /vehicles` (needed as FK dependencies for shipments).

### Validation (before any write)
- `tracking_number` (shipments), `license_number` (drivers), `plate_number` (vehicles) must be unique → **409** if already taken
- `driver_id` / `vehicle_id` on a shipment must reference existing rows → **404** if not
- Field-level constraints (`weight_kg > 0`, `priority` 1–3, required fields, etc.) enforced by the Day 2 Pydantic schemas → **422**, with a flattened, readable `detail` message instead of FastAPI's raw error array

### Transactional write
`POST /shipments` creates the `Shipment` and its `Route` on the same session in a single transaction — proven by test: a request with a non-existent `driver_id` is rejected before any write happens, and a request that fails at the DB layer rolls back cleanly. No orphaned rows are ever left behind.

### Clean responses
All create endpoints return their `*Response` Pydantic schema (never the raw ORM object), so only the fields defined in Day 2's contracts are ever exposed — no internal-only columns, no SQLAlchemy internals.

Error handling lives in `app/exceptions.py` (`NotFoundError`, `ConflictError`) and is mapped to JSON in `app/main.py`'s exception handlers, so every 4xx conforms to the shared `ErrorResponse { detail, error_code }` shape.

Verified with a live server against Postgres:
| Case | Result |
|------|--------|
| Valid shipment + route | 201, full nested response |
| Duplicate `tracking_number` | 409 `duplicate_tracking_number` |
| Nonexistent `driver_id` | 404 `driver_not_found`, no row written |
| `weight_kg` ≤ 0 | 422, clean message |
| `priority` out of range | 422, clean message |
| Missing `route` | 422, clean message |

### Day 4 Status
- [x] Valid data is persisted
- [x] Invalid input returns useful 4xx errors
- [x] Response does not expose internal fields

## Day 5: Read workflows

Implemented `GET /shipments` (list) and `GET /shipments/{id}` (detail) — plus matching list/detail endpoints for `/drivers` and `/vehicles`.

### Pagination
`limit`/`offset` query params (`app/pagination.py`), `limit` bounded 1–100 (default 20), `offset` ≥ 0 — both validated with 422 on bad input. Every list response is wrapped in a `Page` envelope: `{ items, total, limit, offset }`, so the client always knows how many records exist in total, not just how many came back.

### Stable ordering
All list endpoints order by `id ASC` — the primary key, which never changes — rather than `created_at`, since two rows can share a timestamp. Verified: requesting `limit=2` at `offset=0`, `offset=2`, and `offset=4` against 6 seeded shipments returns three non-overlapping pages covering all 6 ids in order, with no duplicates or gaps.

### Filtering (shipments list)
`status`, `driver_id`, `vehicle_id`, `priority` — combinable, all applied before both the count and the page query so `total` always matches the filtered result set, not the whole table.

### Detail endpoints scoped correctly
Each detail endpoint (`/shipments/{id}`, `/drivers/{id}`, `/vehicles/{id}`) fetches by primary key only and returns exactly one resource or a 404 — verified for both existing and non-existent ids on all three.

### Day 5 Status
- [x] List endpoint is paginated
- [x] Detail endpoint is scoped correctly
- [x] Missing records return 404

## Day 6: Update and delete

Implemented `PATCH`/`DELETE` for shipments (primary), drivers, and vehicles.

### Partial updates validate fields
All `PATCH` endpoints use `exclude_unset=True` — only fields present in the request body are changed. Validation before any write:
- **Shipments**: `driver_id`/`vehicle_id`, if changed, must reference existing rows (404). Status changes must follow the lifecycle in `app/shipment_state.py`: `pending → in_transit → delivered`, or `→ cancelled` from `pending`/`in_transit`. `delivered`/`cancelled` are terminal — **any** further edit (not just status) is rejected with 409, since a closed shipment represents something that already happened.
- **Drivers/Vehicles**: `license_number`/`plate_number`, if changed, must stay unique (409 on collision).
- Field-level constraints (e.g. `priority` 1–3) are enforced by the existing `*Update` schemas → 422.

### Delete behavior is explicit
- **Shipment**: only a `pending` shipment can be deleted. Anything `in_transit` or beyond is real activity that must be `cancelled` via `PATCH`, not erased — 409 otherwise. A pending delete cascades to its `Route` (and any `Alert`s) via the ORM's `cascade="all, delete-orphan"` — verified no orphaned `Route` row survives.
- **Driver/Vehicle**: cannot be deleted while it has any `pending`/`in_transit` shipment attached — 409 with the active count. Deletable once those shipments are resolved (delivered/cancelled) or reassigned — verified end-to-end: blocked while active, then succeeded immediately after cancelling the shipment.

### Database constraints remain valid
No update or delete path can produce a shipment pointing at a nonexistent driver/vehicle, a duplicate license/plate number, or an orphaned route. Tested with a live server against Postgres — full matrix below.

| Case | Result |
|------|--------|
| PATCH shipment, partial field only | 200, other fields unchanged |
| PATCH shipment, valid transition (`pending→in_transit→delivered`) | 200 at each step |
| PATCH shipment, invalid transition (`in_transit→pending`) | 409 `invalid_status_transition` |
| PATCH shipment already `delivered` | 409 `shipment_terminal_state` |
| PATCH shipment, `priority=9` | 422 |
| PATCH shipment, nonexistent `driver_id` | 404 |
| PATCH/DELETE nonexistent id (shipment/driver/vehicle) | 404 |
| DELETE shipment, `status=pending` | 204, route cascade-deleted |
| DELETE shipment, `status=delivered` | 409 `shipment_not_deletable` |
| DELETE driver/vehicle with active shipment | 409 `*_has_active_shipments` |
| DELETE driver/vehicle after shipment resolved | 204 |
| PATCH driver/vehicle, duplicate license/plate | 409 |

### Day 6 Status
- [x] Partial updates validate fields
- [x] Delete behavior is explicit
- [x] Database constraints remain valid

## Day 7: Authentication

Added JWT-based authentication (`/auth/register`, `/auth/login`, `/auth/me`) and protected every mutating endpoint (`POST`/`PATCH`/`DELETE`) on shipments, drivers, and vehicles. Read endpoints (`GET`) remain public, since browsing fleet data isn't sensitive in this design — only writing to it is.

### Credentials / tokens are validated
- Passwords hashed with **bcrypt** (`app/security.py`) — never stored or logged in plaintext.
- `POST /auth/login` uses the standard OAuth2 password flow, verifies the password against the stored hash, and issues a **JWT** (HS256, 60-minute expiry by default) via **PyJWT**.
- `GET /auth/me` and every protected route decode and validate that JWT through `get_current_user` (`app/dependencies.py`): checks signature, expiry, and that the user still exists and is active — before the route body ever runs.
- Login failure (wrong password) and login failure (unknown username) return the **identical** 401 message, deliberately, to avoid leaking which usernames are registered.

### Protected routes reject anonymous requests
Every `POST`/`PATCH`/`DELETE` on `/shipments`, `/drivers`, `/vehicles` requires a valid Bearer token — verified live: no token, a garbage token, and an expired/invalid-signature token are all rejected with 401 before hitting the database, while a valid token succeeds normally. `GET` endpoints were re-verified to still work anonymously (they're intentionally public).

### Secrets are loaded from environment variables
`SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` are read from `.env` via `python-dotenv` in `app/security.py` — **not hardcoded**. Verified: temporarily removing `SECRET_KEY` from the environment makes the app **fail to start** with a clear error, rather than silently falling back to an insecure default.

| Case | Result |
|------|--------|
| `POST /auth/register` | 201, password never returned in response |
| `POST /auth/register` duplicate username | 409 |
| `POST /auth/login` correct credentials | 200, JWT issued |
| `POST /auth/login` wrong password / unknown username | 401, identical message both times |
| `GET /auth/me` with valid token | 200, own profile |
| `GET /auth/me` with no token / garbage token | 401 `not_authenticated` / `invalid_token` |
| `POST /shipments` with no token | 401, blocked before DB write |
| `POST /shipments` with valid token | 201, succeeds |
| `PATCH`/`DELETE` with no token | 401 |
| `GET /shipments` with no token | 200 — reads stay public |
| App started with `SECRET_KEY` unset | Fails to start with a clear error |

### Day 7 Status
- [x] Credentials or tokens are validated
- [x] Protected routes reject anonymous requests
- [x] Secrets are loaded from environment variables

```
pulsefleet/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py         # async engine + session
│   ├── models.py            # SQLAlchemy ORM models (incl. User)
│   ├── schemas.py           # Pydantic models + Page[T] + auth schemas
│   ├── exceptions.py        # NotFoundError, ConflictError, UnauthorizedError
│   ├── pagination.py        # shared limit/offset query params
│   ├── shipment_state.py    # status transition state machine
│   ├── security.py          # password hashing + JWT (Day 7)
│   ├── dependencies.py      # get_current_user auth dependency (Day 7)
│   └── routers/
│       ├── __init__.py
│       ├── auth.py          # register, login, me (Day 7)
│       ├── drivers.py       # POST/PATCH/DELETE protected, GET public
│       ├── vehicles.py      # POST/PATCH/DELETE protected, GET public
│       └── shipments.py     # POST/PATCH/DELETE protected, GET public
├── migrations/           # Alembic
│   ├── env.py
│   └── versions/
├── docs/
│   └── api-design.md
├── requirements.txt
├── alembic.ini
├── .env.example
├── .gitignore
└── README.md
```

## Day 8: Authorization

Enforced resource ownership across all three core entities so one user can never read or modify another user's records — including reads, which now also require authentication (a necessary consequence of owner-scoping).

### Every private query is owner-scoped
Added `owner_id` (FK → `users.id`) to `Driver`, `Vehicle`, and `Shipment`. Every query in `app/routers/{drivers,vehicles,shipments}.py` — create, list, detail, update, delete — filters by `owner_id == current_user.id`. A shipment's `driver_id`/`vehicle_id` references are also ownership-checked on create/update: you cannot attach another user's driver or vehicle to your own shipment, even if you know/guess its numeric id.

### Cross-user access returns a controlled error
Reading, updating, or deleting a record that belongs to another user returns the **same 404** used for a record that doesn't exist at all (`shipment_not_found`, `driver_not_found`, `vehicle_not_found`). This is deliberate: a 403 would confirm the id is real and just off-limits, letting someone enumerate valid ids belonging to other tenants. 404 reveals nothing.

### Migration handles pre-existing data safely
The migration (`4f3ac6a69f21`) adds `owner_id` as nullable first, **backfills** any pre-existing rows to the earliest-registered user, then tightens the column to `NOT NULL` with named FK constraints (needed for a clean, reversible `downgrade()`). Verified against real leftover data from earlier days (0 drivers, 3 vehicles, 7 shipments with no owner) — backfill assigned them correctly, and a full downgrade → upgrade cycle re-runs the backfill logic cleanly.

### Ownership tests are included
`tests/test_ownership.py` — 9 tests, run with `pytest` against the real ASGI app and a live Postgres database (no mocking). Each test spins up two independent users via `/auth/register` + `/auth/login` and asserts one can never read/list/update/delete the other's data. Fixed a real pytest-asyncio + async-SQLAlchemy event-loop bug along the way (connections bound to the wrong loop across function-scoped tests) by pinning both fixture and test loop scope to `session` in `pytest.ini`.

| Test | Verifies |
|------|----------|
| `test_owner_can_read_own_shipment` | Baseline: owner can read their own data |
| `test_other_user_cannot_read_shipment` | Cross-user GET → 404 |
| `test_other_user_cannot_update_shipment` | Cross-user PATCH → 404, data genuinely untouched |
| `test_other_user_cannot_delete_shipment` | Cross-user DELETE → 404, record still exists for owner |
| `test_list_shipments_is_owner_scoped` | List never includes another user's shipments, either direction |
| `test_cannot_assign_another_users_driver_to_own_shipment` | Can't attach someone else's driver via a guessed id |
| `test_other_user_cannot_read_or_modify_driver` | Same GET/PATCH/DELETE protection for drivers |
| `test_other_user_cannot_read_or_modify_vehicle` | Same GET/PATCH/DELETE protection for vehicles |
| `test_list_drivers_and_vehicles_is_owner_scoped` | Driver/vehicle lists are also owner-scoped |

Run with:
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

### Day 8 Status
- [x] Every private query is owner-scoped
- [x] Cross-user access returns a controlled error
- [x] Ownership tests are included

```
pulsefleet/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py            # async engine + session
│   ├── models.py               # SQLAlchemy ORM models (incl. User, owner_id FKs)
│   ├── schemas.py               # Pydantic models + Page[T] + auth schemas
│   ├── exceptions.py            # NotFoundError, ConflictError, UnauthorizedError
│   ├── pagination.py            # shared limit/offset query params
│   ├── shipment_state.py        # status transition state machine
│   ├── security.py              # password hashing + JWT
│   ├── dependencies.py          # get_current_user auth dependency
│   └── routers/
│       ├── __init__.py
│       ├── auth.py              # register, login, me
│       ├── drivers.py           # owner-scoped CRUD
│       ├── vehicles.py          # owner-scoped CRUD
│       └── shipments.py         # owner-scoped CRUD
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # async test client + user fixtures
│   └── test_ownership.py        # Day 8 ownership tests
├── migrations/           # Alembic
│   ├── env.py
│   └── versions/
├── docs/
│   └── api-design.md
├── requirements.txt
├── requirements-dev.txt         # pytest, httpx, anyio (test-only deps)
├── pytest.ini
├── alembic.ini
├── .env.example
├── .gitignore
└── README.md
```

## Day 9: Search and Filters

Extended list endpoints on shipments, drivers, and vehicles with search and range filters — every one executed as a SQL `WHERE` clause, never loaded into Python and filtered in memory.

### Filters execute in SQL
- **Shipments**: `q` (ILIKE on tracking_number), `origin`/`destination` (ILIKE on the joined `Route`), `pickup_after`/`pickup_before`, `delivery_after`/`delivery_before` (range on scheduled timestamps), `weight_min`/`weight_max` (range), plus the existing `status`/`driver_id`/`vehicle_id`/`priority` exact-match filters.
- **Drivers**: `q` (ILIKE across name/license_number/phone), `status`.
- **Vehicles**: `q` (ILIKE across plate_number/vehicle_type), `status`, `vehicle_type`, `capacity_min`/`capacity_max`.

All filters (including the owner scope from Day 8) are combined with SQLAlchemy `.where()` calls before the query is ever sent to Postgres — count and page queries only ever touch the rows that actually match, never the full table.

### Empty results are handled
A filter combination matching nothing returns `200` with `{"items": [], "total": 0, ...}` — never a 404 or error. Verified for shipments, drivers, and vehicles.

### Pagination remains correct with filters
`total` reflects the **filtered** count, not the whole table (proven with a mixed dataset: 5 matching + 1 non-matching shipment → `total: 5`). Paging through filtered results with `limit=2` across 3 offsets returns all 5 filtered ids with zero duplicates and zero gaps.

### Bug found and fixed
While wiring this up, found that `drivers.py` was missing its `Optional`/`Query` imports — the search filter code referenced them but nothing imported them, which would have crashed the whole app at startup. Fixed and re-verified the app imports and runs cleanly.

### Tests
`tests/test_search_filters.py` — 7 tests, run against the live app + Postgres:

| Test | Verifies |
|------|----------|
| `test_search_by_tracking_number_substring` | `q` narrows to the matching shipment only |
| `test_filter_by_origin` | Route-joined `origin` filter works |
| `test_weight_range_filter` | Combined range + search filters (AND) |
| `test_invalid_range_returns_422` | `weight_min > weight_max` rejected cleanly |
| `test_empty_result_is_handled_not_errored` | No match → 200, empty page, not an error |
| `test_empty_result_also_handled_for_drivers_and_vehicles` | Same guarantee on the other two resources |
| `test_pagination_remains_correct_with_filters_applied` | `total` is the filtered count; 3 pages cover all 5 matches with no overlap |

Run with `pytest tests/ -v` — 16/16 pass (9 from Day 8 + 7 new).

### Day 9 Status
- [x] Filters execute in SQL
- [x] Empty results are handled
- [x] Pagination remains correct with filters

## Day 10: Service Integration

Integrated an external weather API (Open-Meteo — free, no key required) behind a dedicated service-layer boundary, used by a new `POST /shipments/{id}/evaluate` endpoint to flag weather-related delay risk.

**Note on verification**: this sandbox's network egress is restricted to package registries (pypi, npm, github, etc.) — it cannot reach `api.open-meteo.com` directly. The integration is built against Open-Meteo's real, documented, key-free API shape, but is verified entirely through mocking (`respx` for the HTTP layer, FastAPI dependency overrides for the route layer) rather than a live call. This isn't a workaround — full mockability without touching the network is exactly what the completion guide's third requirement asks for, and it's what a real CI pipeline would do anyway rather than depend on a live third party.

### Route code stays focused
`app/services/weather_service.py` is the **only** file that knows the API's URL, request params, or response shape. The route handler (`evaluate_shipment` in `app/routers/shipments.py`) does exactly three things: check ownership, call `weather_service.assess_route_risk(lat, lon)`, and translate the result into an `Alert` if risk is moderate+. It never touches `httpx`, timeouts, or retry logic directly.

### Timeouts and failures are handled
`WeatherService.assess_route_risk` never raises — every failure mode comes back as a plain `WeatherAssessment(available=False, reason=...)`:
- **Timeout** → one retry, then `reason="timeout"`
- **HTTP error status** (4xx/5xx) → `reason="http_error"`
- **Connection error** → `reason="connection_error"`
- **Malformed/unexpected response shape** → `reason="malformed_response"` (no retry — retrying won't fix a response the API isn't going to reshape)

The endpoint treats an unavailable weather service as a **degraded** result, not a failed request: `POST /shipments/{id}/evaluate` still returns `200` with `weather_data_available: false` and a `note` explaining why, rather than a 500 or 503. A downed third-party API shouldn't block dispatch operations.

### Integration can be mocked in tests
Two independent layers of mocking, both exercised:
- `tests/test_weather_service.py` (9 tests) — mocks `httpx` itself via `respx`, so `WeatherService` is tested against simulated success, timeout, 500, connection error, malformed response, and the retry-then-succeed / retry-exhausted paths. No FastAPI or database involved.
- `tests/test_evaluate_endpoint.py` (7 tests) — mocks at the FastAPI dependency level (`app.dependency_overrides[get_weather_service]`) with a fake service returning canned `WeatherAssessment`s, proving the endpoint's behavior (alert creation, owner-scoping, graceful degradation, auth, validation) without depending on `WeatherService`'s internals at all.

| Test file | Verifies |
|---|---|
| `test_weather_service.py` | Risk classification, timeout/HTTP/connection/malformed-response handling, retry logic |
| `test_evaluate_endpoint.py` | Alert created on high risk, no alert on low risk, graceful 200 on service outage, owner-scoping (Day 8 rule still applies), 404 on missing shipment, 401 without auth, 422 on bad coordinates |

Run with `pytest tests/ -v` — **32/32 pass** (16 from Days 8–9 + 9 weather-service + 7 evaluate-endpoint).

### Day 10 Status
- [x] Timeouts and failures are handled
- [x] Route code stays focused
- [x] Integration can be mocked in tests

```
pulsefleet/
├── app/
│   ├── services/
│   │   ├── __init__.py
│   │   └── weather_service.py   # external API boundary (Day 10)
│   ├── routers/
│   │   └── shipments.py          # + POST /shipments/{id}/evaluate
│   └── ... (unchanged from Day 9)
├── tests/
│   ├── test_weather_service.py   # mocks httpx via respx
│   ├── test_evaluate_endpoint.py # mocks the FastAPI dependency
│   └── ... (unchanged from Day 9)
└── ... (unchanged from Day 9)
```

## Day 11: Reliability

Added structured logging, request-id correlation, a catch-all handler for unexpected failures, and hardened the DB session to guarantee no partial writes survive an error.

### Unexpected failures are logged
`app/logging_config.py` emits one JSON object per log line (grep/query-able, no regex parsing needed). A catch-all `@app.exception_handler(Exception)` in `main.py` logs every unhandled exception — type, message, full traceback, request_id, method, path — via `error_logger.exception(...)`, while the client only ever receives a generic message plus the `request_id` to quote when reporting it. Verified live: a deliberately raised `RuntimeError` produced a full structured JSON log line server-side, while the client got a clean `500` with no exception details.

Expected 4xx outcomes (validation, not-found, conflict, auth) are **not** logged as errors — only genuinely unexpected failures hit `error_logger`, so real problems don't get buried under routine client mistakes.

### Client errors are actionable
Every error response — 4xx or 5xx — now carries a `request_id` alongside `detail`/`error_code`, so a person hitting an error and a log line on the server can be matched up directly. Validation errors (422) already named the specific field (from Day 4); verified this still holds (`weight_kg: ...` style messages, not generic "invalid input").

### Failed writes do not leave partial data
- `get_db` (`app/database.py`) now explicitly rolls back the session on **any** exception before closing — defense-in-depth beyond the per-route `IntegrityError` handling already in place since Day 4, covering failure modes those `except` blocks don't specifically catch.
- Verified: a shipment create rejected for a bad `vehicle_id` (before any row is written) doesn't block reusing the same `tracking_number` on retry — proving no orphaned row was left behind.
- Verified: a duplicate `license_number` on driver create is rejected without corrupting or overwriting the original driver's data.

### Tests
`tests/test_reliability.py` — 5 tests:

| Test | Verifies |
|---|---|
| `test_unhandled_exception_returns_sanitized_500` | Client never sees exception type/message, only `request_id` |
| `test_every_response_carries_a_request_id_header` | `X-Request-ID` present on every response |
| `test_validation_error_names_the_offending_field` | 422 messages are actionable |
| `test_failed_shipment_create_leaves_no_partial_data` | Rejected create leaves no orphaned row |
| `test_failed_driver_create_leaves_no_partial_data` | Duplicate-key rejection doesn't corrupt existing data |

### Day 11 Status
- [x] Unexpected failures are logged
- [x] Client errors are actionable
- [x] Failed writes do not leave partial data

## Day 12: Automated Tests

The single biggest change: **the entire test suite now runs against a dedicated database** (`pulsefleet_test_db`), never the development database — previously (Days 8–10) tests ran against the same Postgres instance as manual dev testing, which worked but wasn't real isolation.

### Tests use an isolated database
`tests/conftest.py` forces `DATABASE_URL` to `TEST_DATABASE_URL` (env-overridable, defaults to `pulsefleet_test_db`) at the very top of the file — before `app.main` (and therefore the SQLAlchemy engine) is ever imported. A session-scoped autouse fixture runs `alembic upgrade head` against that database once per test run, using the exact same migration chain the real app deploys with. Verified empirically: after a full 37-test run, the dev database's row counts matched only my manual curl testing, while the test database held all the test-generated data — genuine separation, not just "should work in theory."

### Happy and failure paths pass
`tests/test_main_workflow.py` — one explicit end-to-end test walking the full user journey in a single readable sequence: register → login → create driver → create vehicle → create shipment → list → get detail → `pending→in_transit` → weather evaluate (mocked) → `in_transit→delivered` → confirm terminal-state lock → delete driver/vehicle. Failure paths (validation errors, 404s, 409s, auth failures) are covered across `test_reliability.py`, `test_search_filters.py`, and the create/update tests threaded through the other files.

### Authorization regression is covered
`tests/test_ownership.py` (Day 8's 9 tests) now runs against the isolated test database as part of the same suite — re-verified passing, so cross-user access protection has an actual regression test, not just a one-time manual check.

### One service failure is covered
`tests/test_evaluate_endpoint.py::test_evaluate_handles_unavailable_weather_service_gracefully` (Day 10) — the weather service reports `available=False`, and the endpoint still returns `200` with a clear `note`, never a `500`.

Full suite: **38/38 passing** — `pytest tests/ -v`:

| File | Count | Covers |
|---|---|---|
| `test_main_workflow.py` | 1 | Full happy-path lifecycle |
| `test_ownership.py` | 9 | Authorization regression |
| `test_reliability.py` | 5 | Logging, sanitized errors, no partial writes |
| `test_search_filters.py` | 7 | SQL filters, empty results, pagination |
| `test_weather_service.py` | 9 | Service failure handling (timeout/HTTP/connection/malformed) |
| `test_evaluate_endpoint.py` | 7 | Endpoint-level service failure + alert creation |

### Day 12 Status
- [x] Tests use an isolated database
- [x] Happy and failure paths pass
- [x] Authorization regression is covered

## Day 13: API Documentation

Added OpenAPI tag metadata and a rich app description (visible in the auto-generated `/docs` and `/redoc` pages), plus `docs/API.md` — the reference the auto-generated docs don't provide on their own: the full auth flow, an endpoint table, every `error_code` the API actually returns, and a worked end-to-end `curl` walkthrough.

`docs/API.md` was written **against the real running app** — the endpoint list and every `error_code` in its reference table were pulled directly from `grep`-ing the codebase and hitting a live `/openapi.json`, not written from memory, so it can't drift from what the code actually does.

Covers:
1. **Auth flow** — register, login (OAuth2 form body, not JSON — explained why), using the Bearer token
2. **Endpoint reference** — all 20 routes, grouped by resource, with auth requirements
3. **Shipment lifecycle** — the state diagram from `shipment_state.py` in plain terms
4. **Filtering/search/pagination** — every filter per resource, from Day 9
5. **Ownership** — the 404-not-403 design decision from Day 8, explained
6. **Error reference** — all 23 distinct `error_code` values the API returns, with HTTP status and meaning
7. **Worked example** — a complete `curl` script for the full shipment lifecycle
8. **Running the tests** — points to Day 12's isolated-DB setup

```
pulsefleet/
├── docs/
│   ├── api-design.md   # Day 2: entity/endpoint design
│   └── API.md           # Day 13: full API reference
└── ... (unchanged from Day 12)
```


## Day 14: Deployment Readiness

Added explicit production configuration, a `Dockerfile`, and a full deployment checklist (`docs/DEPLOYMENT.md`).

### Debug mode is disabled
`app/config.py` hardcodes FastAPI's `debug` flag to `False` — not environment-conditional, not a default that could be flipped by a missing env var. A debug app returns raw Python tracebacks to clients on unhandled errors, which would completely undo Day 11's sanitized-500 design; there's deliberately no way to turn it on. Verified: `app.debug is False`. Interactive docs (`/docs`, `/redoc`, `/openapi.json`) stay configurable via `DOCS_ENABLED` (default on) for deployments that want to hide their API surface — verified both states live.

### Health endpoint checks dependencies
`GET /health` previously just confirmed the process was alive. It now runs `SELECT 1` against the database (3-second timeout) and reports `200`/`{"status": "ok"}` when reachable or `503`/`{"status": "degraded"}` when not — the distinction a load balancer or orchestrator actually needs to know whether to route traffic here. **Verified against a real outage**, not a mock: stopped Postgres mid-run, confirmed `/health` correctly returned `503` with `database: unreachable`, restarted Postgres, confirmed it recovered to `200` without restarting the app itself.

### Secrets are excluded from source control
Re-verified everything already in place since Day 3/7: `.env` in `.gitignore`, never present in any packaged deliverable (checked across all prior zips), no hardcoded secret anywhere in `app/` (grepped). Added `.dockerignore` so building the image can't bake `.env` into a layer either — secrets are injected at deploy time as real environment variables, documented in `docs/DEPLOYMENT.md`.

### Additional deployment readiness work
- `Dockerfile` — multi-stage-free, minimal `python:3.12-slim` image; verified the exact production startup command (`uvicorn ... --workers 2`, no `--reload`) actually starts 2 worker processes cleanly and shuts down properly on SIGTERM. **Not verified with a live `docker build`** — this sandbox has no Docker daemon — but `requirements.txt` installing cleanly into a fresh venv has been re-proven multiple times (Days 12, 15), which is the part most likely to break a container build.
- `docs/DEPLOYMENT.md` — required env vars, secret generation, the health-check contract for probes, migration-before-traffic guidance, the startup command, and a pre-flight checklist.
- 3 new tests in `tests/test_deployment.py` (debug-off, health-check shape, health-check doesn't require auth).

Full suite: **41/41 passing** (38 from Days 8–12 + 3 new).

### Day 14 Status
- [x] Debug mode is disabled
- [x] Health endpoint checks dependencies
- [x] Secrets are excluded from source control

## Day 15: Final Review

A full quality pass, re-run from a genuinely clean state **after** Day 14's changes (config, health check, Dockerfile) landed — not a stale pass from before them.

### Migrations and tests pass from a clean state
1. Dropped and recreated the Postgres role and both databases from scratch (confirmed empty).
2. Fresh `venv`, installed only from `requirements.txt`/`requirements-dev.txt` — no missing dependencies.
3. `alembic upgrade head` on the empty dev DB → all 3 migrations applied cleanly, all 7 tables created.
4. Full regression: `alembic downgrade base` → back to empty → `alembic upgrade head` → clean, lands at head.
5. `pytest tests/` → **41/41 passing** (38 from Days 8–12 + 3 from Day 14's deployment tests), against the freshly-migrated, isolated test database.
6. `pyflakes app/ tests/` → zero warnings across the whole codebase, including the new `app/config.py`.

### Core flow works end to end
Ran the full lifecycle live against the freshly-migrated dev database, now including Day 14's dependency-aware health check:

1. `GET /health` → `200`, `database: ok`
2. Register + log in
3. Create a driver and a vehicle
4. Create a shipment — transactional with its route (Day 4)
5. List + filter (Day 9)
6. `pending → in_transit` (Day 6)
7. A second user attempts to read the first user's shipment → `404`, not `403` (Day 8)
8. Weather-risk evaluation → real network failure in this sandbox, handled gracefully: `200` with `weather_data_available: false` (Day 10 — this is a genuine failure being handled, not a simulated one)
9. `in_transit → delivered`, then a further edit attempt → `409 shipment_terminal_state` (Day 6)
10. **Stopped Postgres mid-demo** → `GET /health` → `503`, `database: unreachable` → restarted Postgres → next `GET /health` → `200` again, no app restart needed (Day 14)

Every step above is real captured output from one live run, not a hypothetical script — see the exact JSON in the terminal transcript.

### README and demo sequence are complete
This file documents all 15 days end to end, plus `docs/api-design.md` (Day 2 design), `docs/API.md` (Day 13 reference), and `docs/DEPLOYMENT.md` (Day 14 checklist).

### Day 15 Status
- [x] Migrations and tests pass from a clean state
- [x] Core flow works end to end
- [x] README and demo sequence are complete
