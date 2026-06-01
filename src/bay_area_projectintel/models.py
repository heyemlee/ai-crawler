from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Category(StrEnum):
    PUBLIC_WORKS = "PUBLIC_WORKS"
    COMMERCIAL_TI = "COMMERCIAL_TI"
    GC_SUBCONTRACT = "GC_SUBCONTRACT"
    RESIDENTIAL_REMODEL = "RESIDENTIAL_REMODEL"
    HOSPITALITY_REMODEL = "HOSPITALITY_REMODEL"
    RESTAURANT_RETAIL = "RESTAURANT_RETAIL"
    OFFICE_LAB = "OFFICE_LAB"
    OTHER = "OTHER"


class RawRecord(BaseModel):
    source: str
    source_record_id: str
    payload: dict[str, Any]
    content_hash: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Company(BaseModel):
    name: str
    license_number: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None


class Contact(BaseModel):
    company_name: str | None = None
    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str


class Project(BaseModel):
    raw_record_id: int
    source: str
    source_record_id: str
    permit_number: str | None = None
    description: str
    project_date: str | None = None
    bid_deadline: str | None = None
    address: str | None = None
    city: str | None = None
    county: str | None = None
    source_url: str | None = None
    company: Company | None = None
    category: Category | None = None
    confidence: float | None = None
    status: str = "new"
    content_hash: str


class ClassificationResult(BaseModel):
    category: Category
    confidence: float
    reason: str
