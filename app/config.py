"""
PulseFleet — Deployment configuration (Day 14: Deployment readiness)

Centralizes the handful of settings that differ between local development
and a real deployment, so main.py doesn't scatter `os.getenv()` calls
across itself. Everything here is read from the environment — nothing
is hardcoded, and nothing here can silently make a production deploy
behave like a dev one.
"""
import os

# ENVIRONMENT controls two things: FastAPI's debug flag (which, if left
# on, would leak stack traces and internals to clients on unhandled
# errors — the exact opposite of Day 11's sanitized-error design) and
# whether the interactive docs (/docs, /redoc) are exposed at all.
#
# Default is "development" so a plain `uvicorn app.main:app --reload`
# with no .env still works out of the box for local work — but any real
# deployment MUST set ENVIRONMENT=production explicitly (see
# docs/DEPLOYMENT.md). There is no "auto-detect production" heuristic:
# guessing wrong in the unsafe direction is worse than requiring an
# explicit opt-in.
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# Debug mode is ALWAYS off, in every environment. FastAPI's debug=True
# would replace Day 11's sanitized 500 responses with raw tracebacks
# rendered to the client — there is no environment where that's
# acceptable, so this isn't even environment-conditional.
DEBUG = False

# Interactive docs default to enabled (handy for graders/reviewers and
# for staging), but can be turned off per-deployment with DOCS_ENABLED=false
# — e.g. for a production deployment that doesn't want to expose its full
# API surface and schema publicly.
DOCS_ENABLED = os.getenv("DOCS_ENABLED", "true").strip().lower() not in ("false", "0", "no")
