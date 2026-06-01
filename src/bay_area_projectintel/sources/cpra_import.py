"""One-shot CPRA CSV import: turn a city's records-act response into raw_records.

Cities answer CPRA requests as CSV (or Excel saved as CSV). Each row is one
permit; the columns vary by city. We map them into the same payload shape the
Socrata adapter produces, then reuse the existing `_normalize_socrata` path —
no new normalizer, no new schema.

Workflow:
  projectintel import-cpra --file data/cpra-inbox/sanjose-2026q2.csv \\
                           --jurisdiction "San Jose"

The portal yaml (`config/cpra/portals.yaml`) may override `field_map` per city
when the column headers differ from the template defaults.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from bay_area_projectintel.config import SocrataFieldMap, SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.models import RawRecord


# Default column names match docs/cpra-request-template.md so the happy path
# needs zero per-city configuration.
DEFAULT_FIELD_MAP = SocrataFieldMap(
    record_id="permit_number",
    permit_number="permit_number",
    description=["description", "permit_type"],
    address=["address"],
    project_date=["issued_date"],
    city=None,  # fall back to the jurisdiction on the portal entry
    company_name="contractor_name",
    company_license="contractor_license",
    company_email="contractor_email",
    company_phone="contractor_phone",
)


class CpraPortal(BaseModel):
    jurisdiction: str
    county: str | None = None
    portal_url: str | None = None
    platform_hint: str | None = None
    contact_fallback: str | None = None
    notes: str | None = None
    # Optional per-city override; missing fields fall back to DEFAULT_FIELD_MAP.
    field_map: dict[str, Any] | None = None


@dataclass
class ImportStats:
    rows_read: int
    raw_inserted: int
    raw_changed: int
    skipped: int


def load_portals(path: Path) -> list[CpraPortal]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [CpraPortal.model_validate(entry) for entry in doc.get("portals") or []]


def portal_for(portals: list[CpraPortal], jurisdiction: str) -> CpraPortal | None:
    needle = _slug(jurisdiction)
    for portal in portals:
        if _slug(portal.jurisdiction) == needle:
            return portal
    return None


def source_name_for(jurisdiction: str) -> str:
    """Stable per-city source key so re-imports upsert instead of duplicating."""
    return f"cpra-{_slug(jurisdiction)}"


def build_source_config(portal: CpraPortal) -> SourceConfig:
    """Synthetic SourceConfig so `normalize_raw_record` treats CPRA rows like Socrata."""
    raw_map = dict(DEFAULT_FIELD_MAP.model_dump())
    if portal.field_map:
        raw_map.update(portal.field_map)
    return SourceConfig(
        type="socrata_building_permits",  # reuses the existing normalizer
        name=f"CPRA — {portal.jurisdiction}",
        jurisdiction=portal.jurisdiction,
        county=portal.county or "",
        access="manual",
        date_field="issued_date",
        field_map=SocrataFieldMap.model_validate(raw_map),
    )


def iter_csv_records(
    csv_path: Path,
    source_name: str,
    field_map: SocrataFieldMap,
) -> Iterable[tuple[RawRecord, bool]]:
    """Yield (record, skipped). `skipped` rows have no usable identifier."""
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        seen_ids: set[str] = set()
        for index, row in enumerate(reader, start=1):
            # Pass numbers and dates through as strings — that matches the Socrata
            # adapter's payload shape and avoids surprising _date_only / regexes.
            payload: dict[str, Any] = {key: (value or "").strip() for key, value in row.items() if key}

            permit_number = ""
            if field_map.permit_number:
                permit_number = str(payload.get(field_map.permit_number) or "").strip()

            # Fall back to a content-hash id only if the city really has no permit
            # number — otherwise re-imports must upsert deterministically.
            record_id = permit_number or stable_hash(payload)
            if record_id in seen_ids:
                # Duplicate rows inside the same CSV — keep the first, log the rest.
                yield (
                    RawRecord(
                        source=source_name,
                        source_record_id=f"__dup_{index}",
                        payload=payload,
                        content_hash=stable_hash(payload),
                    ),
                    True,
                )
                continue
            seen_ids.add(record_id)

            yield (
                RawRecord(
                    source=source_name,
                    source_record_id=record_id,
                    payload=payload,
                    content_hash=stable_hash(payload),
                ),
                False,
            )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
