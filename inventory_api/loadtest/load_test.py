#!/usr/bin/env python3
"""A small async load-testing tool for the Inventory API.

Fires a fixed number of requests at the running API with a bounded number
of requests in flight at once (concurrency), then reports latency
percentiles and throughput -- similar in spirit to `ab`/`hey`/`wrk`, but
scenario-aware so it can seed a product and hit the cached read path,
exercise writes, or mix several endpoints together.

Usage:
    uv run loadtest/load_test.py
    uv run loadtest/load_test.py --scenario mixed --requests 200 --concurrency 20
    uv run loadtest/load_test.py --base-url http://localhost:8080 --json-out results.json

Scenarios:
    read    (default) Seeds one product, then issues GET /products/{id}
            repeatedly -- all requests after the first are served from the
            Redis cache, so this is the "Robyn is shockingly fast" path.
    list    GET /products repeatedly.
    create  POST /products with a unique body each time -- a write-heavy,
            uncached path that hits Postgres on every call.
    health  GET /health repeatedly -- measures pure framework/runtime
            overhead with no database or cache involved.
    mixed   A weighted mix of the above (40% read, 30% list, 20% create,
            10% health), assigned per-request -- a rough approximation of
            real traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestResult:
    label: str
    status_code: int | None
    duration_ms: float
    ok: bool
    error: str | None = None


@dataclass
class Report:
    scenario: str
    total_requests: int
    concurrency: int
    wall_time_s: float
    results: list[RequestResult] = field(default_factory=list)

    @property
    def successes(self) -> list[RequestResult]:
        return [r for r in self.results if r.ok]

    @property
    def failures(self) -> list[RequestResult]:
        return [r for r in self.results if not r.ok]

    def status_code_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            key = str(r.status_code) if r.status_code is not None else "error"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def latency_stats(self) -> dict[str, float]:
        durations = sorted(r.duration_ms for r in self.successes)
        if not durations:
            return {}
        return {
            "min": durations[0],
            "mean": statistics.fmean(durations),
            "median": _percentile(durations, 50),
            "p90": _percentile(durations, 90),
            "p95": _percentile(durations, 95),
            "p99": _percentile(durations, 99),
            "max": durations[-1],
            "stdev": statistics.pstdev(durations) if len(durations) > 1 else 0.0,
        }

    def throughput_rps(self) -> float:
        if self.wall_time_s <= 0:
            return 0.0
        return self.total_requests / self.wall_time_s


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile -- simple and good enough for a benchmark tool."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1)))))
    return sorted_values[k]


_NOT_RUNNING_HINT = (
    "Is the API running?\n"
    "  - Deploy + benchmark in one step : ./loadtest/run.sh\n"
    "  - Or deploy it yourself first    : docker compose up -d --build\n"
    "  - Then re-run this command, or add --wait-timeout 60 to wait for it to come up."
)


async def wait_for_health(client: httpx.AsyncClient, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = await client.get("/health", timeout=5)
            if resp.status_code == 200:
                print(f"API is healthy at {client.base_url}")
                return
        except httpx.HTTPError as err:
            last_error = err
        await asyncio.sleep(1)
    raise RuntimeError(f"API did not become healthy within {timeout_s}s (last error: {last_error})\n{_NOT_RUNNING_HINT}")


async def ensure_reachable(client: httpx.AsyncClient, wait_timeout: float) -> None:
    """Confirms the API responds before the benchmark starts.

    With ``wait_timeout > 0`` this polls (useful right after a deploy that's
    still starting up). Otherwise it does a single quick check, so a clear,
    actionable error is raised immediately instead of a confusing low-level
    connection error partway through seeding the benchmark.
    """
    if wait_timeout > 0:
        await wait_for_health(client, wait_timeout)
        return

    try:
        resp = await client.get("/health", timeout=5)
        if resp.status_code == 200:
            return
        raise RuntimeError(f"API at {client.base_url} responded to /health with status {resp.status_code}.\n{_NOT_RUNNING_HINT}")
    except httpx.HTTPError as err:
        raise RuntimeError(f"Could not reach the API at {client.base_url} ({err}).\n{_NOT_RUNNING_HINT}") from err


async def _timed_request(client: httpx.AsyncClient, label: str, method: str, url: str, **kwargs) -> RequestResult:
    start = time.perf_counter()
    try:
        resp = await client.request(method, url, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000
        return RequestResult(label=label, status_code=resp.status_code, duration_ms=duration_ms, ok=resp.is_success)
    except httpx.HTTPError as err:
        duration_ms = (time.perf_counter() - start) * 1000
        return RequestResult(label=label, status_code=None, duration_ms=duration_ms, ok=False, error=str(err))


def _product_payload() -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Load Test Widget {suffix}",
        "category": "load-test",
        "price": 9.99,
        "quantity": 100,
    }


async def _seed_product(client: httpx.AsyncClient) -> int:
    resp = await client.post("/products", json=_product_payload(), timeout=10)
    resp.raise_for_status()
    return resp.json()["id"]


async def run_scenario(
    scenario: str,
    base_url: str,
    total_requests: int,
    concurrency: int,
    request_timeout: float,
) -> Report:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=request_timeout) as client:
        product_id: int | None = None
        if scenario in ("read", "mixed"):
            product_id = await _seed_product(client)
            # Warm the cache so the timed "read" requests all hit Redis, not
            # Postgres -- this is the fast path the article is about.
            await client.get(f"/products/{product_id}")
            print(f"Seeded product {product_id} and warmed the cache")

        semaphore = asyncio.Semaphore(concurrency)

        async def bound_request(index: int) -> RequestResult:
            async with semaphore:
                if scenario == "health":
                    return await _timed_request(client, "health", "GET", "/health")
                if scenario == "list":
                    return await _timed_request(client, "list", "GET", "/products")
                if scenario == "read":
                    return await _timed_request(client, "read", "GET", f"/products/{product_id}")
                if scenario == "create":
                    return await _timed_request(client, "create", "POST", "/products", json=_product_payload())
                if scenario == "mixed":
                    # Deterministic weighting by index, not randomness, so
                    # results are reproducible across runs: 4 read, 3 list,
                    # 2 create, 1 health out of every 10 requests.
                    bucket = index % 10
                    if bucket < 4:
                        return await _timed_request(client, "read", "GET", f"/products/{product_id}")
                    if bucket < 7:
                        return await _timed_request(client, "list", "GET", "/products")
                    if bucket < 9:
                        return await _timed_request(client, "create", "POST", "/products", json=_product_payload())
                    return await _timed_request(client, "health", "GET", "/health")
                raise ValueError(f"Unknown scenario: {scenario}")

        start = time.perf_counter()
        results = await asyncio.gather(*(bound_request(i) for i in range(total_requests)))
        wall_time_s = time.perf_counter() - start

    return Report(
        scenario=scenario,
        total_requests=total_requests,
        concurrency=concurrency,
        wall_time_s=wall_time_s,
        results=list(results),
    )


def print_report(report: Report) -> None:
    print()
    print("=" * 60)
    print(f"Load test report -- scenario: {report.scenario}")
    print("=" * 60)
    print(f"Total requests     : {report.total_requests}")
    print(f"Concurrency        : {report.concurrency}")
    print(f"Wall-clock time    : {report.wall_time_s:.3f}s")
    print(f"Throughput         : {report.throughput_rps():.2f} req/s")
    print(f"Successful         : {len(report.successes)}")
    print(f"Failed             : {len(report.failures)}")

    print("\nStatus code breakdown:")
    for code, count in sorted(report.status_code_counts().items()):
        print(f"  {code:>6}: {count}")

    stats = report.latency_stats()
    if stats:
        print("\nLatency (ms, successful requests only):")
        print(f"  min    : {stats['min']:.2f}")
        print(f"  mean   : {stats['mean']:.2f}")
        print(f"  median : {stats['median']:.2f}")
        print(f"  p90    : {stats['p90']:.2f}")
        print(f"  p95    : {stats['p95']:.2f}")
        print(f"  p99    : {stats['p99']:.2f}")
        print(f"  max    : {stats['max']:.2f}")
        print(f"  stdev  : {stats['stdev']:.2f}")

    if report.failures:
        print("\nFirst few failures:")
        for r in report.failures[:5]:
            print(f"  [{r.label}] status={r.status_code} error={r.error}")
    print("=" * 60)


def report_to_dict(report: Report) -> dict:
    return {
        "scenario": report.scenario,
        "total_requests": report.total_requests,
        "concurrency": report.concurrency,
        "wall_time_s": report.wall_time_s,
        "throughput_rps": report.throughput_rps(),
        "successes": len(report.successes),
        "failures": len(report.failures),
        "status_code_counts": report.status_code_counts(),
        "latency_ms": report.latency_stats(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8080", help="Base URL of the running API")
    parser.add_argument("-n", "--requests", type=int, default=100, help="Total number of requests to make")
    parser.add_argument("-c", "--concurrency", type=int, default=10, help="Max number of requests in flight at once")
    parser.add_argument(
        "--scenario",
        choices=["read", "list", "create", "health", "mixed"],
        default="read",
        help="Which endpoint(s) to hammer",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=0.0,
        help="If > 0, poll /health for up to this many seconds before starting the benchmark",
    )
    parser.add_argument("--json-out", default=None, help="Optional path to write the report as JSON")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> Report:
    async with httpx.AsyncClient(base_url=args.base_url) as client:
        await ensure_reachable(client, args.wait_timeout)

    report = await run_scenario(
        scenario=args.scenario,
        base_url=args.base_url,
        total_requests=args.requests,
        concurrency=args.concurrency,
        request_timeout=args.timeout,
    )
    return report


def main() -> int:
    args = parse_args()
    try:
        report = asyncio.run(main_async(args))
    except (RuntimeError, httpx.HTTPError) as err:
        print(f"Load test failed: {err}", file=sys.stderr)
        return 1

    print_report(report)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report_to_dict(report), f, indent=2)
        print(f"\nJSON report written to {args.json_out}")

    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
