# Deployment

StoreOps AI runs as four processes plus two managed services:

| Component | What it is | Scales by |
|---|---|---|
| `web` | Next.js app (also proxies `/api/*` to the API) | instances |
| `api` | FastAPI (`uvicorn app.main:app`) | instances |
| `worker` | Arq worker on the default queue | instances (each runs up to 50 jobs) |
| `worker-priority` | Arq worker on `storeops:priority` (Pro stores) | instances |
| PostgreSQL 16 | system of record | managed |
| Redis 7 | job queue, live event streams, sessions, rate limits | managed |

Scheduled work (simulator ticks, abandoned-cart scans, proposal expiry, nightly Shopify
reconciliation, weekly review summaries, retention and post-uninstall purges) runs as Arq cron jobs
inside the workers. Arq's `unique` cron jobs make running several worker instances safe.

## Backend on Render

`render.yaml` is a Blueprint for the API, both workers, PostgreSQL and Key Value (Redis).

1. In Render, **New → Blueprint** and select this repository.
2. Create the `storeops-secrets` environment group from `.env.example`. At minimum set:
   - `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `TOKEN_ENCRYPTION_KEY`:
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `FRONTEND_ORIGIN` and `CORS_ORIGINS`: your web URL, e.g. `https://app.example.com`
   - `PUBLIC_APP_URL`: the same web URL (Shopify redirects and webhooks go through it)
   - `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `RESEND_API_KEY`, `EMAIL_FROM` for live stores
3. Deploy. Migrations run as the pre-deploy command (`alembic upgrade head`). The API health check
   is `/ready`, which verifies PostgreSQL and Redis.

The API refuses to start in production with an unsafe configuration (default or short
`SECRET_KEY`, missing `TOKEN_ENCRYPTION_KEY`, insecure cookies or a non-HTTPS frontend origin).
`postgres://` URLs from the provider are converted to the asyncpg driver automatically.

The same images run on Fly.io or Railway: build `backend/Dockerfile` once, run it three times
with commands `uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers`,
`arq app.worker.main.WorkerSettings` and the same with `WORKER_QUEUE=storeops:priority`.

## Frontend on Vercel

1. Import the repository and set the **root directory** to `frontend`.
2. Set `API_INTERNAL_URL` to the API's public URL (e.g. `https://storeops-api.onrender.com`).
3. Add your domain, then set the backend's `FRONTEND_ORIGIN`, `CORS_ORIGINS` and `PUBLIC_APP_URL`
   to it.

Because the browser only talks to the web origin, session cookies stay first-party. Live updates
use Server-Sent Events through the same proxy. If a platform buffers or times out long-lived
responses, the dashboard switches to polling on its own, and its connection badge shows
"Live (polling)".

Alternatively run the `runner` stage of `frontend/Dockerfile` (Next.js standalone output) next to
the API.

## Observability

- **Logs:** JSON lines on stdout with a request id (`x-request-id` is accepted and returned).
- **Errors:** set `SENTRY_DSN`. Cookies, auth headers, CSRF tokens and request bodies are scrubbed
  before sending.
- **Traces:** set `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP/HTTP, e.g. `http://otel-collector:4318`).
  FastAPI, SQLAlchemy, Redis and outbound HTTPX calls are instrumented. Set `RELEASE` to tag
  deploys.
- **Probes:** `/health` (liveness), `/ready` (PostgreSQL + Redis).

## Performance

`make bench` ingests and processes simulated orders against the configured database and reports
acknowledgement time, throughput and latency. On a 4-vCPU development container with PostgreSQL
and Redis on the same machine:

| Metric | Result | Target |
|---|---|---|
| Webhook acknowledgement (store + enqueue) | ~6 ms | < 1 s |
| Single-event processing (normalise, all agents, live publish) | p50 ~31 ms | < 2 s end to end |
| Throughput per worker process | ~50 events/s | 50 events/s |

TLS to PostgreSQL costs roughly 15% throughput on the same host. Scale workers horizontally for
more.
