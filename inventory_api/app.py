"""Inventory API -- a production-shaped Robyn service.

Robyn combines a Rust runtime with a Python developer experience: routing,
HTTP parsing, and connection handling live in Rust, while everything below
stays in Python. No ASGI configuration, no Uvicorn, no lifespan
boilerplate -- just ``Robyn(__file__)``.
"""

import structlog
from robyn import Robyn, Request
from robyn.exceptions import HTTPException
from robyn.openapi import OpenAPI, OpenAPIInfo
from robyn.ws import WebSocketDisconnect

from config import settings
from database import dispose_engine, init_models
from middleware.auth import JWTAuthentication, create_access_token
from middleware.logging import log_request
from middleware.rate_limit import rate_limiter
from routes.product_routes import register_product_routes
from schemas import HealthResponse, MeResponse, TokenRequest, TokenResponse
from services.product_service import ProductNotFoundError, redis as product_cache
from utils import json_response

logger = structlog.get_logger()

# Swagger UI is served at /docs and the raw spec at /openapi.json -- both
# are wired up automatically by Robyn; this just gives them real content.
app = Robyn(
    __file__,
    openapi=OpenAPI(
        info=OpenAPIInfo(
            title=settings.APP_NAME,
            version="1.0.0",
            description=(
                "A production-shaped inventory service built with Robyn: "
                "thin route handlers, a service layer, Redis-backed caching, "
                "JWT authentication, and Redis-backed rate limiting."
            ),
        )
    ),
)


# --- Exception handling --------------------------------------------------
# Registered before any routes: Robyn captures `app.exception_handler` at
# the moment each route is added, so a handler defined after a route would
# silently not apply to it.
@app.exception
def handle_exception(error: Exception):
    if isinstance(error, HTTPException):
        return json_response({"error": error.detail}, error.status_code)
    if isinstance(error, ProductNotFoundError):
        return json_response({"error": str(error)}, 404)

    logger.error("unhandled_exception", error=str(error))
    return json_response({"error": "Internal Server Error"}, 500)


# --- Authentication ----------------------------------------------------
# Configuring this once lets any route opt into auth with `auth_required=True`.
app.configure_authentication(JWTAuthentication())

# --- Global middleware --------------------------------------------------
app.before_request()(log_request)
app.before_request()(rate_limiter)

# --- Routes --------------------------------------------------------------
register_product_routes(app)


@app.get(
    "/health",
    openapi_name="Health check",
    openapi_tags=["Health"],
    response_model=HealthResponse,
)
async def health(request: Request):
    """Reports whether the service process itself is up.

    Deliberately does not depend on Postgres/Redis -- use per-request
    errors to detect those, not this endpoint.
    """
    return {
        "status": "healthy",
        "service": "inventory",
        "version": "1.0.0",
    }


@app.post(
    "/auth/token",
    openapi_name="Issue a demo access token",
    openapi_tags=["Auth"],
    response_model=TokenResponse,
)
async def issue_token(request: Request, body: TokenRequest):
    """Mints a JWT for the given email.

    There's no password check here on purpose -- this is a demo login
    endpoint. Swap it for real credential verification before this goes
    anywhere near production.
    """
    token = create_access_token(subject=body.email, email=body.email)
    return {"access_token": token, "token_type": "bearer"}


@app.get(
    "/me",
    auth_required=True,
    openapi_name="Current user",
    openapi_tags=["Auth"],
    response_model=MeResponse,
)
async def profile(request: Request):
    """Returns the claims encoded in the caller's bearer token."""
    identity = request.identity
    return {"user": identity.claims if identity else {}}


@app.websocket("/notifications")
async def notifications(websocket):
    try:
        while True:
            event = await websocket.receive_text()
            await websocket.send_text(f"Received: {event}")
    except WebSocketDisconnect:
        pass


async def on_startup():
    logger.info("starting_up", app=settings.APP_NAME)
    try:
        await init_models()
    except Exception as err:
        # Robyn's Rust runtime propagates an exception raised here as a hard
        # panic (crashing the whole process) rather than a Python traceback,
        # so a database that isn't reachable yet must not escape this
        # handler. Log it clearly instead -- routes that touch the database
        # will still fail individually (and get caught by `handle_exception`)
        # until the database comes up.
        logger.error(
            "database_unavailable_at_startup",
            error=str(err),
            hint="Is Postgres running and DATABASE_URL correct? See README.md.",
        )


async def on_shutdown():
    logger.info("shutting_down", app=settings.APP_NAME)
    await dispose_engine()
    await product_cache.close()


app.startup_handler(on_startup)
app.shutdown_handler(on_shutdown)


if __name__ == "__main__":
    app.start(host=settings.HOST, port=settings.PORT)
