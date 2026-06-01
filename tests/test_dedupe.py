from pathlib import Path

from bay_area_projectintel.db import Database
from bay_area_projectintel.models import Company, Project, RawRecord
from bay_area_projectintel.pipeline.dedupe import (
    DedupeRecord,
    address_block_key,
    dedupe_projects,
    find_duplicate_groups,
    is_duplicate,
    normalize_address,
    plan_duplicates,
)


def rec(id, desc, addr, *, source="datasf", company=False, first_seen="2026-05-01") -> DedupeRecord:
    return DedupeRecord(id=id, description=desc, address=addr, source=source, has_company=company, first_seen=first_seen)


def test_normalize_address_canonicalizes_abbreviations_and_units() -> None:
    # Suite/unit markers are canonicalized (number kept) so different suites stay distinct.
    assert normalize_address("123 Main Street, Suite 200") == "123 main st unit 200"
    assert normalize_address("123 Main St #200") == "123 main st unit 200"
    assert normalize_address("123 North Main Avenue") == "123 n main ave"
    assert normalize_address(None) == ""


def test_address_block_key_groups_same_street() -> None:
    assert address_block_key("123 Main Street") == address_block_key("123 Main St, Unit 4")
    assert address_block_key("123 Main St") != address_block_key("456 Main St")


def test_is_duplicate_matches_same_address_near_identical_title() -> None:
    a = rec(1, "Reroof single family residence", "123 Oak Ave")
    b = rec(2, "Re-roof single family residence", "123 Oak Avenue", source="marin")
    assert is_duplicate(a, b)


def test_is_duplicate_rejects_different_address() -> None:
    a = rec(1, "Reroof single family residence", "123 Oak Ave")
    b = rec(2, "Reroof single family residence", "987 Elm Ave")
    assert not is_duplicate(a, b)


def test_is_duplicate_rejects_same_address_unrelated_title() -> None:
    a = rec(1, "Solar panel installation on roof", "123 Main St")
    b = rec(2, "Demolish detached garage structure", "123 Main St")
    assert not is_duplicate(a, b)


def test_is_duplicate_keeps_different_suites_distinct() -> None:
    # Two tenant improvements in the same building, different suites: distinct leads.
    a = rec(1, "Tenant improvement office", "500 Market St Suite 200")
    b = rec(2, "Tenant improvement office", "500 Market St Suite 450")
    assert not is_duplicate(a, b)


def test_no_address_never_duplicates() -> None:
    a = rec(1, "Reroof single family residence", None)
    b = rec(2, "Reroof single family residence", None)
    assert not is_duplicate(a, b)
    assert find_duplicate_groups([a, b]) == []


def test_find_duplicate_groups_clusters_cross_source() -> None:
    records = [
        rec(1, "Reroof single family residence", "123 Oak Ave", source="datasf"),
        rec(2, "Re-roof single family residence", "123 Oak Avenue #1", source="marin"),
        rec(3, "Brand new office tenant improvement", "500 Market St", source="datasf"),
    ]
    groups = find_duplicate_groups(records)
    assert groups == [[1, 2]]


def test_plan_duplicates_keeps_record_with_company() -> None:
    records = [
        rec(1, "Reroof single family residence", "123 Oak Ave", company=False, first_seen="2026-05-01"),
        rec(2, "Reroof single family residence", "123 Oak Ave", company=True, first_seen="2026-05-10"),
    ]
    # id 2 has a company, so it should be canonical even though it was seen later.
    assert plan_duplicates(records) == [(1, 2)]


def _seed_project(db: Database, source_id: str, description: str, address: str, company: Company | None) -> int:
    raw = RawRecord(source="datasf", source_record_id=source_id, payload={"k": source_id}, content_hash=source_id)
    raw_id, _ = db.upsert_raw_record(raw)
    project = Project(
        raw_record_id=raw_id,
        source="datasf",
        source_record_id=source_id,
        description=description,
        address=address,
        company=company,
        content_hash=source_id,
    )
    return db.upsert_project(project)


def test_dedupe_projects_marks_duplicates_and_hides_from_export(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite3")
    db.migrate()
    id_a = _seed_project(db, "a", "Reroof single family residence", "123 Oak Ave", None)
    id_b = _seed_project(
        db, "b", "Re-roof single family residence", "123 Oak Avenue", Company(name="Acme Builders Inc")
    )
    _seed_project(db, "c", "New office tenant improvement", "500 Market St", None)

    stats = dedupe_projects(db)
    assert stats == {"projects": 3, "duplicates": 1, "groups": 1}

    exported_ids = {row["id"] for row in db.export_rows()}
    # The record with a company (id_b) is canonical and kept; id_a is hidden.
    assert id_b in exported_ids
    assert id_a not in exported_ids
    assert len(exported_ids) == 2

    # Re-running is idempotent.
    assert dedupe_projects(db)["duplicates"] == 1
