# Inventory API

A small, production-shaped example service built with [Robyn](https://robyn.tech),
the Rust-powered Python web framework. It follows the architecture from the
article *"Robyn: The Rust-Powered Python Framework That's Shockingly Fast"*:
thin route handlers, a service layer for business rules, a repository layer
for persistence, Redis caching, structured logging, JWT authentication, rate
limiting, a health check, a WebSocket endpoint, and interactive API docs at
`/docs`.

## Project layout

```
inventory_api/
├── app.py                          # App wiring: routes, middleware, auth, lifecycle
├── config.py                       # Pydantic settings, sourced from env / .env
├── database.py                     # Async SQLAlchemy engine + session factory
├── models.py                       # ORM models (Product)
├── schemas.py                      # Pydantic request/response models (validation + /docs)
├── repositories/
│   └── product_repository.py       # Persistence logic, no framework code
├── services/
│   └── product_service.py          # Business rules + Redis caching
├── routes/
│   └── product_routes.py           # Thin HTTP handlers
├── middleware/
│   ├── auth.py                     # JWT AuthenticationHandler
│   ├── logging.py                  # Structured request logging
│   └── rate_limit.py               # Redis fixed-window rate limiter
├── loadtest/
│   ├── load_test.py                # Async load-testing/benchmark tool
│   ├── run.sh                      # Deploys via Docker, then runs the benchmark
│   ├── docker-compose.loadtest.yml # Compose override (raises the rate limit for benchmarking)
│   └── README.md                   # Load testing guide + sample results
├── requirements.txt
├── Dockerfile
└── docker-compose.yml              # Postgres + Redis + the API, for local runs
```

## Requirements

- Python 3.14+
- PostgreSQL (for product storage)
- Redis (for caching, rate limiting)

## Running locally

### Option 1: Docker Compose (easiest)

```sh
docker compose up --build
```

This starts Postgres, Redis, and the API together on `http://localhost:8080`.

### Option 2: Local Python environment

This project uses [uv](https://docs.astral.sh/uv/) for dependency management
(a `requirements.txt` is also provided for `pip`).

```sh
uv sync
```

or

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and point `DATABASE_URL` / `REDIS_URL` at a
running Postgres and Redis instance:

```sh
cp .env.example .env
```

Then start the app:

```sh
uv run app.py
# or, with the venv activated:
python app.py
```

Tables are created automatically on startup (fine for a demo -- use Alembic
migrations for a real production schema).

### Option 3: Native install with an existing Postgres, no Docker

If Postgres is already running on the host and you'd rather not containerize
anything (or Docker isn't available), the defaults in `config.py` already
point at `localhost` for both Postgres and Redis, so no code changes are
needed -- just environment setup:

1. **Create the database** (adjust user/password to match your existing
   Postgres setup):
   ```sh
   sudo -u postgres psql -c "CREATE DATABASE inventory;"
   ```
   Then set `DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/inventory`
   in `.env`. asyncpg connects over TCP (not the `peer`-authenticated Unix
   socket `psql` uses by default), so make sure `pg_hba.conf` allows
   password auth (`md5`/`scram-sha-256`) for `127.0.0.1`/`localhost`.

2. **Set up Redis** -- required for caching and rate limiting to actually do
   anything useful. Easiest option if Docker is available, without
   containerizing the whole stack:
   ```sh
   docker run -d --name inventory-redis -p 6379:6379 --restart unless-stopped redis:7-alpine
   ```
   or install it natively: `sudo apt update && sudo apt install -y redis-server && sudo systemctl enable --now redis-server`.
   `REDIS_URL=redis://localhost:6379/0` (the default) needs no change either
   way. If you skip Redis entirely, the app still runs -- the rate limiter
   fails open and product reads fall through to Postgres on every request
   (see `middleware/rate_limit.py` / `services/product_service.py`) -- but
   you lose the caching benefit and will see `rate_limiter_unavailable` /
   `product_cache_read_failed` warnings in the logs.

3. **Install deps and run**, same as Option 2:
   ```sh
   uv sync
   cp .env.example .env   # then edit DATABASE_URL / REDIS_URL / JWT_SECRET
   uv run app.py
   ```

4. **Load testing in this mode**: `loadtest/run.sh` and
   `loadtest/docker-compose.loadtest.yml` are Docker-Compose-specific, since
   they deploy the whole stack. Here, just start the app (step 3 above) and
   call the tool directly once `/health` responds:
   ```sh
   uv run loadtest/load_test.py --scenario read --requests 100 --concurrency 10 --wait-timeout 30
   ```
   Without the compose override there's nothing raising the default rate
   limit, so bump `RATE_LIMIT_PER_MINUTE` in `.env` before benchmarking or
   you'll see `429`s after ~100 requests/minute.

## Trying it out

```sh
# Health check
curl http://localhost:8080/health

# Create a product
curl -X POST http://localhost:8080/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Widget", "category": "hardware", "price": 9.99, "quantity": 100}'

# List products
curl http://localhost:8080/products

# Fetch a single product (served from Redis on repeat requests)
curl http://localhost:8080/products/1

# Adjust stock
curl -X POST http://localhost:8080/products/1/stock \
  -H "Content-Type: application/json" \
  -d '{"quantity": 42}'

# Mint a demo JWT (no password check -- for demo purposes only)
curl -X POST http://localhost:8080/auth/token \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'

# Call an authenticated route
curl http://localhost:8080/me -H "Authorization: Bearer <token from above>"
```

WebSocket notifications are available at `ws://localhost:8080/notifications`
(any message sent is echoed back with a `Received:` prefix).

## API docs

Robyn auto-generates interactive Swagger UI docs from the routes' Pydantic
models and OpenAPI metadata (`openapi_name`, `openapi_tags`, `response_model`,
`responses`). While the server is running, open:

- `http://localhost:8080/docs` -- interactive Swagger UI, including a
  "Authorize" button for testing `auth_required` routes like `/me` with a
  bearer token.
- `http://localhost:8080/openapi.json` -- the raw OpenAPI 3.1 spec.

Request bodies (`ProductCreate`, `StockAdjustment`, `TokenRequest` in
`schemas.py`) are validated automatically; a malformed body returns a `422`
with field-level error details instead of reaching the handler.

## Load testing / benchmarking

```sh
./loadtest/run.sh
```

Deploys the API with Docker Compose, waits for it to become healthy, then
fires 100 requests at it with up to 10 concurrent -- reporting throughput
and latency percentiles. See `loadtest/README.md` for scenarios (cached
reads, uncached reads, writes, a mixed-traffic blend, and a pure
framework-overhead health check) and sample results.

## Design notes

- **Thin handlers, fat services.** Route handlers in `routes/` only
  orchestrate a DB session, repository, and service call. Business rules
  live in `services/product_service.py`.
- **Repository pattern.** `repositories/product_repository.py` has no
  Robyn-specific code -- it would survive a framework migration untouched.
- **Caching first.** `ProductService.fetch_product` checks Redis before
  touching Postgres, and invalidates the cache on writes.
- **Auth via Robyn's `AuthenticationHandler`.** Any route can opt into auth
  with `@app.get(..., auth_required=True)`; `middleware/auth.py` verifies the
  bearer JWT and exposes claims via `request.identity.claims`.
- **Rate limiting is Redis-based**, not in-memory, so it works correctly
  across multiple worker processes.
