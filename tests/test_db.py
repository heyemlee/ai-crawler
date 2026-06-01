from datetime import UTC, datetime

from bay_area_projectintel.db import Database, stable_hash
from bay_area_projectintel.models import Company, Project, RawRecord


def test_raw_record_upsert_is_idempotent(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    payload = {"record_id": "abc", "description": "test"}
    record = RawRecord(
        source="datasf-building-permits",
        source_record_id="abc",
        payload=payload,
        content_hash=stable_hash(payload),
        fetched_at=datetime.now(UTC),
    )

    first_id, first_changed = db.upsert_raw_record(record)
    second_id, second_changed = db.upsert_raw_record(record)

    assert first_id == second_id
    assert first_changed is True
    assert second_changed is False


def test_update_company_license_info_persists(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    payload = {"record_id": "p1", "description": "x"}
    raw_id, _ = db.upsert_raw_record(
        RawRecord(source="datasf", source_record_id="p1", payload=payload, content_hash="p1")
    )
    project_id = db.upsert_project(
        Project(
            raw_record_id=raw_id,
            source="datasf",
            source_record_id="p1",
            description="remodel",
            company=Company(name="Acme Builders", license_number="492944"),
            content_hash="p1",
        )
    )
    rows = db.get_projects_for_enrichment()
    company_id = next(r["company_id"] for r in rows if r["id"] == project_id)

    db.update_company_license_info(company_id, "555 Main St SF CA", "CLEAR", "B")

    with db.connect() as conn:
        company = conn.execute(
            "SELECT address, license_status, license_classification FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
    assert company["address"] == "555 Main St SF CA"
    assert company["license_status"] == "CLEAR"
    assert company["license_classification"] == "B"
