# PulseFleet — Deployment Checklist

## 1. Required environment variables

Set these as real environment variables on the host/platform — **never** commit a
populated `.env` file (see [§4](#4-secrets)). `.env.example` documents every variable;
copy it and fill in real values per environment.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://user:pass@host:5432/dbname` |
| `SECRET_KEY` | yes | JWT signing key. Long, random, unique per environment. The app **refuses to start** if this is unset (see `app/security.py`) — this is deliberate, not a bug. |
| `JWT_ALGORITHM` | no (default `HS256`) | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no (default `60`) | |
| `WEATHER_API_BASE_URL` | no (default Open-Meteo) | |
| `WEATHER_API_TIMEOUT_SECONDS` | no (default `5`) | |
| `ENVIRONMENT` | **yes for production** | `development` (default) or `production`. See [§2](#2-debug-mode). |
| `DOCS_ENABLED` | no (default `true`) | Set `false` to hide `/docs`, `/redoc`, `/openapi.json` |
| `TEST_DATABASE_URL` | test runs only | Never used at runtime, only by `pytest` |

Generate a real `SECRET_KEY`:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 2. Debug mode

FastAPI's `debug` flag is **hardcoded to `False`** in `app/config.py` — there is no
environment variable that can turn it on. A debug FastAPI app returns raw Python
tracebacks to clients on unhandled errors, which would completely undo the sanitized
500-response design from [Day 11](README.md#day-11-reliability). This is intentional:
guessing wrong toward "leak details" is worse than never having the option.

Do still set `ENVIRONMENT=production` explicitly in production — it controls the
`environment` field reported by `/health` (useful for confirming you're looking at the
deployment you think you are) and is the natural place to hang any future
prod-vs-dev behavior.

## 3. Health check

`GET /health` — **no auth required** (a load balancer/orchestrator has no token).

- Actually attempts `SELECT 1` against the database (3-second timeout), not just a
  "the process is alive" check. Returns:
  - `200 {"status": "ok", "checks": {"database": "ok"}, ...}` when the DB is reachable
  - `503 {"status": "degraded", "checks": {"database": "unreachable"|"timeout"}, ...}` when it isn't
- **Verified against a real outage**, not just simulated: stopped Postgres, confirmed
  `/health` returned `503` with `database: unreachable`, restarted Postgres, confirmed
  it recovered to `200` on the next request — no restart of the app itself needed.
- Point your platform's readiness/liveness probe at this path. A `503` here means
  "don't route traffic here yet / pull this instance out of rotation," not "kill it" —
  the process itself is fine, it just can't serve real requests without the DB.

## 4. Secrets

- `.env` is in `.gitignore` — verified it was never present in any packaged deliverable
  throughout this project (checked at Day 14, but this has held since Day 3).
- `.dockerignore` also excludes `.env` — building the Docker image never bakes secrets
  into a layer.
- No secret is hardcoded anywhere in `app/` — verified via `grep`.
- In production, inject `DATABASE_URL` and `SECRET_KEY` via your platform's secret
  manager / env var injection (e.g. a PaaS's config vars, a Kubernetes Secret, Docker
  Compose's `env_file` pointed at a file that is itself gitignored) — never bake them
  into the image or commit them anywhere.

## 5. Running migrations before first traffic

```bash
alembic upgrade head
```

Run this once against the target database before starting the app (or as a pre-deploy
step in CI/CD). The app does **not** run migrations automatically on startup — a fresh
deployment against an unmigrated database will fail on the first request that touches a
missing table. Note that `/health` only checks connectivity, not schema, so it can
report `200` even against an unmigrated database; it isn't a substitute for running
this step. Migrate first, always.

## 6. Startup command

**Do not** use `--reload` outside local development — it disables multi-worker support
and adds file-watching overhead with no benefit in production.

```bash
# Plain uvicorn, multi-worker (simplest; matches the Dockerfile's CMD)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

# Or via Docker
docker build -t pulsefleet .
docker run -p 8000:8000 --env-file .env pulsefleet
```

Tune `--workers` to available CPU cores (a common starting point: `2 × cores + 1`).

## 7. Pre-flight checklist

- [ ] `SECRET_KEY` set to a real random value, unique to this environment
- [ ] `DATABASE_URL` points at the real production database
- [ ] `ENVIRONMENT=production` set
- [ ] `alembic upgrade head` run against the production database
- [ ] `.env` is **not** present in the deployed artifact/image (only real env vars are)
- [ ] `GET /health` returns `200` once deployed
- [ ] `pytest tests/` passes against a throwaway test database before deploying (never
      against production)
