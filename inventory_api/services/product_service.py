"""Business rules for products.

Business rules don't belong inside route handlers -- keeping them here
means the HTTP layer stays incredibly small, and exactly how production
APIs should look.
"""

import json

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import settings
from models import Product
from repositories.product_repository import ProductRepository

# One Redis client, reused across requests. Caching is where Robyn begins to
# feel genuinely fast: your framework processes requests in microseconds,
# but your database almost certainly doesn't.
redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
logger = structlog.get_logger()


class ProductNotFoundError(Exception):
    """Raised when a requested product doesn't exist."""


class ProductService:
    def __init__(self, repository: ProductRepository):
        self.repository = repository

    async def fetch_product(self, product_id: int) -> dict:
        cache_key = self._cache_key(product_id)

        # The cache is an optimization, not a dependency -- if Redis is down,
        # fall straight through to the database rather than failing the
        # request.
        try:
            cached = await redis.get(cache_key)
        except RedisError as err:
            logger.error("product_cache_read_failed", error=str(err))
            cached = None
        if cached:
            return json.loads(cached)

        product = await self.repository.get(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found")

        payload = self._serialize(product)
        try:
            await redis.setex(cache_key, settings.PRODUCT_CACHE_TTL_SECONDS, json.dumps(payload))
        except RedisError as err:
            logger.error("product_cache_write_failed", error=str(err))
        return payload

    async def list_products(self, limit: int = 50, offset: int = 0) -> list[dict]:
        products = await self.repository.list(limit=limit, offset=offset)
        return [self._serialize(product) for product in products]

    async def create_product(
        self,
        *,
        name: str,
        category: str | None,
        price: float,
        quantity: int,
    ) -> dict:
        product = await self.repository.create(
            name=name,
            category=category,
            price=price,
            quantity=quantity,
        )
        return self._serialize(product)

    async def adjust_stock(self, product_id: int, quantity: int) -> dict:
        product = await self.repository.update_quantity(product_id, quantity)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found")

        # The record changed -- drop the stale cache entry instead of serving
        # outdated stock levels for the remainder of the TTL.
        try:
            await redis.delete(self._cache_key(product_id))
        except RedisError as err:
            logger.error("product_cache_invalidate_failed", error=str(err))
        return self._serialize(product)

    @staticmethod
    def _cache_key(product_id: int) -> str:
        return f"product:{product_id}"

    @staticmethod
    def _serialize(product: Product) -> dict:
        return {
            "id": product.id,
            "name": product.name,
            "category": product.category,
            "price": float(product.price),
            "quantity": product.quantity,
        }
