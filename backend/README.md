# StoreOps AI — backend

FastAPI API, Arq workers and Alembic migrations.

```bash
uv sync                                  # install
uv run alembic upgrade head              # migrate
uv run uvicorn app.main:app --reload     # API on :8000
uv run arq app.worker.main.WorkerSettings  # worker
```

Quality gates:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
uv run pytest --cov
```

Tests need PostgreSQL (database `storeops_test`, override with `DATABASE_URL`). Redis is faked
in-process.

## Layout

| Path | Purpose |
|---|---|
| `app/core` | settings, logging, database / Redis clients, crypto, rate limiting |
| `app/models` | SQLAlchemy models (multi-tenant: every tenant table carries `shop_id`) |
| `app/auth` | password hashing, Redis-backed sessions |
| `app/api` | routers, dependencies (auth, CSRF, RBAC), request/response schemas |
| `app/services` | domain logic shared by API and workers |
| `app/worker` | Arq worker settings and jobs |
| `alembic` | migrations |
