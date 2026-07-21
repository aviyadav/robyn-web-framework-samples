# Load testing the Inventory API

A small async load-testing tool (`load_test.py`) plus an orchestration
script (`run.sh`) that deploys the API with Docker Compose, waits for it to
become healthy, and runs the benchmark against it.

## Quick start

```sh
# From the inventory_api/ directory:
./loadtest/run.sh
```

This will:

1. `docker compose up -d --build` -- builds and starts the API, Postgres,
   and Redis.
2. Poll `GET /health` until the API responds (up to 60s).
3. Run the default benchmark: **100 requests, 10 concurrent**, against the
   cached `GET /products/{id}` read path.
4. Tear the stack back down (pass `--keep-up` to leave it running).

Any extra arguments are passed straight through to `load_test.py`, e.g.:

```sh
./loadtest/run.sh --scenario mixed --requests 500 --concurrency 25 --keep-up
```

## Running against an already-running API

`load_test.py` does **not** deploy anything itself -- it just sends HTTP
requests to `--base-url` (default `http://localhost:8080`). If you call it
directly instead of via `run.sh`, the API must already be up, or you'll get:

```
Load test failed: Could not reach the API at http://localhost:8080 (All connection attempts failed).
```

Deploy it first (with the load-test rate-limit override, so you don't get
`429`s partway through the benchmark -- see below), then run the tool:

```sh
docker compose -f docker-compose.yml -f loadtest/docker-compose.loadtest.yml up -d --build
uv run loadtest/load_test.py --scenario read --requests 100 --concurrency 10

# when you're done:
docker compose -f docker-compose.yml -f loadtest/docker-compose.loadtest.yml down
```

If the API might still be starting up (e.g. you just ran `docker compose up
-d` and containers are mid-healthcheck), add `--wait-timeout 60` to poll
until it's ready instead of failing immediately.

## Scenarios

| Scenario | What it hits | Notes |
|---|---|---|
| `read` (default) | `GET /products/{id}` | Seeds one product and warms the cache first, so all 100 timed requests are Redis cache hits -- the "fast path" the article is about. |
| `list` | `GET /products` | Always a Postgres query (not cached). |
| `create` | `POST /products` | Write-heavy: one `INSERT` per request, unique body each time. |
| `health` | `GET /health` | No database/Redis involved -- measures pure Robyn/Rust runtime + rate-limiter overhead. |
| `mixed` | A weighted blend | 40% read, 30% list, 20% create, 10% health, deterministically assigned so runs are reproducible. |

## A note on the rate limiter

The app's default rate limit is 100 requests/minute/IP (see
`config.py`/`middleware/rate_limit.py`) -- sensible for a real deployment,
but it means a benchmark hitting the API from a single machine will start
seeing `429`s well before you learn anything about the API's own
performance. `docker-compose.loadtest.yml` is a Compose override that raises
`RATE_LIMIT_PER_MINUTE` for load-testing only; `run.sh` applies it
automatically. The base `docker-compose.yml` is untouched, so a normal
`docker compose up` still gets the production-sane default.

## Sample results

Measured on the machine this project was built on (single Docker host,
Postgres 16 + Redis 7 containers, API container with 1 Actix worker) -- your
numbers will vary with hardware, but the *shape* should be similar. Each row
is a separate run: 100 requests, concurrency 10.

| Scenario | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |
|---|---:|---:|---:|---:|---:|
| `health` | 1258 | 6.5 | 11.7 | 27.9 | 29.7 |
| `read` (cached) | 1295 | 6.2 | 14.8 | 24.3 | 24.9 |
| `mixed` | 1253 | 7.0 | 11.6 | 16.2 | 18.0 |
| `list` (uncached DB read) | 607 | 6.8 | 97.7 | 99.1 | 99.2 |
| `create` (DB write) | 427 | 12.6 | 56.2 | 58.4 | 58.5 |

Takeaways from this run:

- **Cached reads (`read`) perform about as well as `health`**, which has no
  database or cache dependency at all -- Redis is doing its job, and the
  claim that "the fastest code is the code that never executes" holds up
  here: a cache hit costs almost nothing extra over the framework's own
  overhead.
- **`list` has a long tail** (p95/p99 jump to ~100ms vs a ~7ms median) --
  with only 10 concurrent requests against a single Postgres connection
  pool and a single Actix worker, a handful of requests queue up waiting for
  a DB round-trip. This is the predicted cost of an uncached, unindexed
  `SELECT ... LIMIT` scan, not a Robyn limitation.
- **`create` is the slowest scenario**, as expected: every request is a
  real `INSERT` plus a commit, with no caching possible for writes.
- All scenarios completed 100/100 successfully once the rate limiter was
  raised for the benchmark; the `read` run against the *default* rate limit
  correctly got a handful of `429`s after ~97 requests in the same
  minute -- confirming the limiter itself works as designed.

Re-run `./loadtest/run.sh` to regenerate these numbers on your own machine;
raw JSON reports land in `loadtest/results/` (gitignored).
