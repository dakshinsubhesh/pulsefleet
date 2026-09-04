# PulseFleet — Predictive Exception & Delay Alert System

15-day Python backend development track — Transport / Logistics domain.

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

### Project structure

```
pulsefleet/
├── app/
│   ├── __init__.py
│   └── main.py
├── requirements.txt
├── .gitignore
└── README.md
```

## Day 2: API Design & Contracts

Defined the core PulseFleet entities and their relationships:

- Driver
- Vehicle
- Shipment
- Route
- Alert

Created Pydantic request and response schemas in `app/schemas.py`.

Documented the REST API endpoint plan, request/response contracts,
success cases, validation errors, not-found errors, and conflict cases
in `docs/api-design.md`.

### Predictive Evaluation Endpoint

`POST /shipments/{id}/evaluate`

This endpoint is planned as the trigger for PulseFleet's predictive
exception and delay evaluation system.

Day 3: Database & Persistence

Configured PostgreSQL 17 as the database for PulseFleet.

Database Stack
PostgreSQL 17
SQLAlchemy
asyncpg
Alembic
Async database sessions
Database Configuration

The async database connection is configured in:

app/database.py

The application uses SQLAlchemy's asynchronous engine and request-scoped database sessions.

ORM Models

Created SQLAlchemy ORM models for the five core entities:

Driver
Vehicle
Shipment
Route
Alert

Models and their relationships are defined in:

app/models.py

Foreign keys and entity relationships match the API design defined during Day 2.

Database Migrations

Alembic was configured for database schema migrations.

Migration configuration:

alembic.ini
migrations/env.py

The initial migration creates the core PulseFleet tables.

Migration Verification

Successfully applied the migration using:

alembic upgrade head

Current migration:

b6187da16e99 (head)

The database connection was also verified successfully using the async SQLAlchemy engine.

Day 3 Status
PostgreSQL database configured
Async SQLAlchemy connection configured
ORM models created
Relationships and foreign keys configured
Alembic migration configured
Initial migration successfully applied
Database connection verified
Project Structure
pulsefleet/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   └── schemas.py
│
├── docs/
│   └── api-design.md
│
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── <migration-file>
│
├── .env.example
├── .gitignore
├── alembic.ini
├── README.md
└── requirements.txt



