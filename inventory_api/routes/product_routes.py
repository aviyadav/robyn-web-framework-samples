"""HTTP layer for products.

Notice how little framework code exists here -- each handler mostly
orchestrates dependencies (a session, a repository, a service) and returns
whatever the service gives back. Everything important lives elsewhere,
which is usually a sign you're building something maintainable instead of
merely functional.

Request bodies are typed with Pydantic models from ``schemas.py``: Robyn
validates them automatically (a malformed body gets a 422 for free). The
``response_model``/``responses`` decorator arguments document the response
shapes at ``/docs`` without constraining what the handler actually returns
(a plain dict on success, an explicit ``Response`` for errors).
"""

from robyn import Request

from database import SessionLocal
from repositories.product_repository import ProductRepository
from schemas import ErrorResponse, ProductCreate, ProductOut, StockAdjustment
from services.product_service import ProductNotFoundError, ProductService
from utils import json_response

_NOT_FOUND_RESPONSE = {"description": "Product not found", "model": ErrorResponse}


def register_product_routes(app) -> None:
    @app.get(
        "/products",
        openapi_name="List products",
        openapi_tags=["Products"],
        response_model=list[ProductOut],
    )
    async def list_products(request: Request):
        """Returns a page of products, ordered by id."""
        limit = int(request.query_params.get("limit", "50") or "50")
        offset = int(request.query_params.get("offset", "0") or "0")

        async with SessionLocal() as session:
            service = ProductService(ProductRepository(session))
            return await service.list_products(limit=limit, offset=offset)

    @app.get(
        "/products/:product_id",
        openapi_name="Get a product",
        openapi_tags=["Products"],
        response_model=ProductOut,
        responses={404: _NOT_FOUND_RESPONSE},
    )
    async def get_product(request: Request, product_id: str):
        """Fetches a single product by id. Served from Redis when cached."""
        async with SessionLocal() as session:
            service = ProductService(ProductRepository(session))
            try:
                return await service.fetch_product(int(product_id))
            except ProductNotFoundError:
                return json_response({"error": "Product not found"}, 404)

    @app.post(
        "/products",
        openapi_name="Create a product",
        openapi_tags=["Products"],
        status_code=201,
        response_model=ProductOut,
    )
    async def create_product(request: Request, body: ProductCreate):
        """Creates a new product."""
        async with SessionLocal() as session:
            service = ProductService(ProductRepository(session))
            product = await service.create_product(
                name=body.name,
                category=body.category,
                price=body.price,
                quantity=body.quantity,
            )
            return json_response(product, 201)

    @app.post(
        "/products/:product_id/stock",
        openapi_name="Adjust stock",
        openapi_tags=["Products"],
        response_model=ProductOut,
        responses={404: _NOT_FOUND_RESPONSE},
    )
    async def adjust_stock(request: Request, product_id: str, body: StockAdjustment):
        """Sets a product's stock quantity and invalidates its cache entry."""
        async with SessionLocal() as session:
            service = ProductService(ProductRepository(session))
            try:
                return await service.adjust_stock(int(product_id), body.quantity)
            except ProductNotFoundError:
                return json_response({"error": "Product not found"}, 404)
