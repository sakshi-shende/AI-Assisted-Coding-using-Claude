"""Pydantic v2 request and response schemas for ExpenseFlow."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseCreate(BaseModel):
    """Request body for submitting a new expense."""

    description: str = Field(min_length=1)
    amount_minor: int = Field(gt=0, description="Amount in integer minor units of `currency`.")
    currency: str = Field(min_length=3, max_length=3, description="ISO-4217 3-letter currency code.")
    category: str = Field(min_length=1)
    submitted_by: str = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        value = value.upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a 3-letter ISO-4217 code")
        return value


class ApprovalRequest(BaseModel):
    """Request body for approving or rejecting a pending expense."""

    reviewed_by: str = Field(min_length=1)


class ExpenseOut(BaseModel):
    """Response body representing a persisted expense."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    submitted_by: str
    description: str
    category: str
    amount_minor: int
    currency: str
    amount_base_minor: int
    fx_rate_numerator: int
    fx_rate_denominator: int
    status: str
    reviewed_by: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
