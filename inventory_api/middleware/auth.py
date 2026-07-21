"""Authentication middleware.

Authentication shouldn't appear inside every endpoint -- it's moved into a
Robyn ``AuthenticationHandler`` here instead. Once configured with
``app.configure_authentication()``, any route declared with
``auth_required=True`` automatically rejects requests without a valid
bearer token, and handlers can read ``request.identity.claims`` for the
caller's data.
"""

from datetime import datetime, timedelta, timezone

import jwt
from robyn.authentication import AuthenticationHandler, BearerGetter
from robyn.robyn import Identity, Request

from config import settings


def create_access_token(subject: str, **extra_claims: str) -> str:
    """Issues a signed JWT for ``subject``.

    Demo helper for the ``/auth/token`` route -- wire this to real user
    credential checks (and a database-backed user table) in production.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
        **extra_claims,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


class JWTAuthentication(AuthenticationHandler):
    """Verifies the ``Authorization: Bearer <token>`` header on a request."""

    def __init__(self):
        super().__init__(token_getter=BearerGetter())

    def authenticate(self, request: Request) -> Identity | None:
        token = self.token_getter.get_token(request)
        if token is None:
            return None

        try:
            claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        except jwt.PyJWTError:
            return None

        return Identity(claims={key: str(value) for key, value in claims.items() if key not in {"iat", "exp"}})
