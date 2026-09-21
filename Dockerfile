# PulseFleet — production image
FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg/psycopg build if needed, kept minimal
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .

# No .env is copied into the image — secrets are injected at runtime via
# real environment variables (see docs/DEPLOYMENT.md). Copying .env into
# an image would bake secrets into every layer/registry copy of it.

EXPOSE 8000

# No --reload (that's a dev-only flag that also disables multi-worker
# support); ENVIRONMENT=production must be set at deploy time (via the
# platform's env var mechanism), not baked in here, so the same image
# works for staging/production without a rebuild.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
