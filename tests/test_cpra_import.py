"""CPRA CSV import tests: portal lookup, field map, raw + normalize round-trip."""

from __future__ import annotations

import json
from pathlib import Path

from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.sources.cpra_import import (
    build_source_config,
    iter_csv_records,
    load_portals,
    portal_for,
    source_name_for,
)


PORTALS_YAML = """
portals:
  - jurisdiction: San Jose
    county: Santa Clara
    platform_hint: NextRequest
  - jurisdiction: Saratoga
    county: Santa Clara
    field_map:
      permit_number: PermitID
      description: [Scope]
      address: [SiteAddress]
      project_date: [IssueDate]
      company_name: Contractor
      company_license: LicenseNumber
      company_phone: ContractorPhone
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------- Portal yaml loading ----------


def test_load_portals_parses_minimal_entry(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    assert [p.jurisdiction for p in portals] == ["San Jose", "Saratoga"]
    assert portals[0].platform_hint == "NextRequest"
    assert portals[0].field_map is None


def test_portal_for_is_case_and_space_insensitive(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    assert portal_for(portals, "san jose").jurisdiction == "San Jose"
    assert portal_for(portals, "SARATOGA").jurisdiction == "Saratoga"
    assert portal_for(portals, "Cupertino") is None


def test_source_name_for_is_stable_slug() -> None:
    assert source_name_for("San Jose") == "cpra-san-jose"
    assert source_name_for("Saratoga") == "cpra-saratoga"


# ---------- Default field map happy path ----------


DEFAULT_CSV = """permit_number,permit_type,description,address,issued_date,contractor_name,contractor_license,contractor_phone,contractor_email
BLD-001,T.I.,Restaurant kitchen remodel,100 Market St,2026-04-15,Acme Builders Inc,492944,(408) 555-1212,info@acmebuilders.com
BLD-002,New Construction,Three-story office,200 First St,2026-04-20,,,,
"""


def test_default_field_map_imports_template_csv(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    portal = portal_for(portals, "San Jose")
    source_config = build_source_config(portal)
    csv_path = _write(tmp_path, "sanjose.csv", DEFAULT_CSV)

    records = list(iter_csv_records(csv_path, source_name_for("San Jose"), source_config.field_map))

    assert len(records) == 2
    record, skipped = records[0]
    assert skipped is False
    assert record.source_record_id == "BLD-001"
    assert record.payload["contractor_name"] == "Acme Builders Inc"

    # Round-trip through the existing normalizer — proves we did not need a
    # CPRA-specific normalize path.
    row = {
        "id": 1,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }
    project = normalize_raw_record(row, source_config)
    assert project.company is not None
    assert project.company.name == "Acme Builders Inc"
    assert project.company.license_number == "492944"
    assert project.company.email == "info@acmebuilders.com"
    assert project.company.phone == "(408) 555-1212"
    assert project.city == "San Jose"  # falls back to jurisdiction since field_map.city is None


# ---------- Per-city field_map override ----------


SARATOGA_CSV = """PermitID,Scope,SiteAddress,IssueDate,Contractor,LicenseNumber,ContractorPhone
SAR-77,New ADU,500 Big Basin Way,2026-03-05,Hilltop Construction,1056789,4081234567
"""


def test_per_city_field_map_override(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    portal = portal_for(portals, "Saratoga")
    source_config = build_source_config(portal)
    csv_path = _write(tmp_path, "saratoga.csv", SARATOGA_CSV)

    records = list(iter_csv_records(csv_path, source_name_for("Saratoga"), source_config.field_map))
    assert len(records) == 1
    record, _ = records[0]
    assert record.source_record_id == "SAR-77"

    row = {
        "id": 1,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }
    project = normalize_raw_record(row, source_config)
    assert project.company is not None
    assert project.company.name == "Hilltop Construction"
    assert project.company.license_number == "1056789"
    assert project.company.phone == "(408) 123-4567"
    assert project.permit_number == "SAR-77"
    assert project.address == "500 Big Basin Way"


# ---------- Dedup within a single CSV ----------


DUPLICATE_CSV = """permit_number,description,address,issued_date,contractor_name
BLD-001,first row,100 Market St,2026-04-15,Acme Builders Inc
BLD-001,duplicate row,100 Market St,2026-04-15,Acme Builders Inc
"""


def test_duplicate_permit_within_csv_is_flagged_skipped(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    portal = portal_for(portals, "San Jose")
    source_config = build_source_config(portal)
    csv_path = _write(tmp_path, "dups.csv", DUPLICATE_CSV)

    records = list(iter_csv_records(csv_path, source_name_for("San Jose"), source_config.field_map))

    assert [skipped for _, skipped in records] == [False, True]


# ---------- Missing permit_number falls back to content hash ----------


NO_PERMIT_CSV = """permit_number,description,address,issued_date,contractor_name
,No permit number,100 Market St,2026-04-15,Acme Builders Inc
"""


def test_missing_permit_number_uses_content_hash(tmp_path) -> None:
    portals = load_portals(_write(tmp_path, "portals.yaml", PORTALS_YAML))
    portal = portal_for(portals, "San Jose")
    source_config = build_source_config(portal)
    csv_path = _write(tmp_path, "nopermit.csv", NO_PERMIT_CSV)

    records = list(iter_csv_records(csv_path, source_name_for("San Jose"), source_config.field_map))

    record, skipped = records[0]
    assert skipped is False
    # Content-hash IDs are 64 hex chars — never an empty string or a permit number.
    assert len(record.source_record_id) == 64
