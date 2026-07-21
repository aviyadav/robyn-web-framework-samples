"""Pydantic schemas.

These serve two purposes at once: Robyn auto-validates request bodies
against them (returning a 422 with details on failure) and uses their
JSON Schema to document requests/responses at ``/docs``.
"""

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Widget"])
    category: str | None = Field(default=None, max_length=100, examples=["hardware"])
    price: float = Field(..., gt=0, examples=[9.99])
    quantity: int = Field(default=0, ge=0, examples=[100])


class ProductOut(BaseModel):
    id: int
    name: str
    category: str | None
    price: float
    quantity: int


class StockAdjustment(BaseModel):
    quantity: int = Field(..., ge=0, examples=[42])


class TokenRequest(BaseModel):
    email: str = Field(..., examples=["you@example.com"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class MeResponse(BaseModel):
    user: dict[str, str]


class ErrorResponse(BaseModel):
    error: str
