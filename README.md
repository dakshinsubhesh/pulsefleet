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
