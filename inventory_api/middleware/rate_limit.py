"""Simple Redis-backed rate limiting.

Fast servers still need protection. This is a fixed-window limiter: cheap,
predictable, and good enough for most services that just need to stop a
single client from hammering the API.
"""

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import settings

redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
logger = structlog.get_logger()


async def check_rate_limit(ip: str) -> bool:
    key = f"rate:{ip}"
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, 60)
    return current <= settings.RATE_LIMIT_PER_MINUTE


async def rate_limiter(request):
    """Global ``before_request`` hook. Returns a 429 once the limit is hit.

    Fails *open* -- if Redis itself is unreachable, requests are allowed
    through rather than every request in the app breaking. Protection is
    best-effort; availability of the actual API is not something a rate
    limiter should be able to take down.
    """
    ip = request.ip_addr or "unknown"
    try:
        allowed = await check_rate_limit(ip)
    except RedisError as err:
        logger.error("rate_limiter_unavailable", error=str(err))
        return request
    if not allowed:
        return {"error": "Too many requests"}, {}, 429
    return request
