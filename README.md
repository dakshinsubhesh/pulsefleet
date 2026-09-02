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
