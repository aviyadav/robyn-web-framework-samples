"""Persistence logic for products.

Nothing here is Robyn-specific. Good architecture survives framework
migrations -- frameworks come and go, well-designed boundaries rarely do.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Product


class ProductRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, product_id: int) -> Product | None:
        stmt = select(Product).where(Product.id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, limit: int = 50, offset: int = 0) -> list[Product]:
        stmt = select(Product).order_by(Product.id).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        category: str | None,
        price: float,
        quantity: int,
    ) -> Product:
        product = Product(name=name, category=category, price=price, quantity=quantity)
        self.session.add(product)
        await self.session.commit()
        await self.session.refresh(product)
        return product

    async def update_quantity(self, product_id: int, quantity: int) -> Product | None:
        product = await self.get(product_id)
        if product is None:
            return None
        product.quantity = quantity
        await self.session.commit()
        await self.session.refresh(product)
        return product
