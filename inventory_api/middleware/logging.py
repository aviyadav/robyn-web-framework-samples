"""Structured request logging.

If production fails at 3:17 AM, ``print()`` isn't going to help. JSON logs
are dramatically easier to search in systems like Elasticsearch, Loki, or
Grafana than free-form strings.
"""

import structlog

logger = structlog.get_logger()


async def log_request(request):
    """Global ``before_request`` hook -- runs before every route handler."""
    logger.info(
        "incoming_request",
        method=request.method,
        path=request.url.path,
        ip=request.ip_addr,
    )
    return request
