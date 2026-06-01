from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnrichmentResult:
    provider: str
    status: str
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    detail: str | None = None
    # CSLB license metadata (stored for later use; not part of the contact gate).
    address: str | None = None
    license_status: str | None = None
    license_classification: str | None = None
