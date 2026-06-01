from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import Category, Company, Project, RawRecord


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def stable_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    changed INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(source, source_record_id)
                );

                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_name TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    license_number TEXT,
                    website TEXT,
                    email TEXT,
                    phone TEXT,
                    address TEXT,
                    license_status TEXT,
                    license_classification TEXT,
                    first_seen TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_record_id INTEGER NOT NULL UNIQUE REFERENCES raw_records(id) ON DELETE CASCADE,
                    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                    source TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    permit_number TEXT,
                    description TEXT NOT NULL,
                    project_date TEXT,
                    bid_deadline TEXT,
                    address TEXT,
                    city TEXT,
                    county TEXT,
                    source_url TEXT,
                    category TEXT,
                    confidence REAL,
                    status TEXT NOT NULL DEFAULT 'new',
                    content_hash TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    exported_at TEXT,
                    duplicate_of INTEGER,
                    UNIQUE(source, source_record_id)
                );

                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
                    name TEXT,
                    role TEXT,
                    email TEXT,
                    phone TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_watermarks (
                    source TEXT PRIMARY KEY,
                    watermark TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_cache (
                    prompt_hash TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS enrichment_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
            if "duplicate_of" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN duplicate_of INTEGER")
            if "bid_deadline" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN bid_deadline TEXT")
            company_columns = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
            for column in ("address", "license_status", "license_classification"):
                if column not in company_columns:
                    conn.execute(f"ALTER TABLE companies ADD COLUMN {column} TEXT")

    def upsert_raw_record(self, record: RawRecord) -> tuple[int, bool]:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, content_hash FROM raw_records WHERE source = ? AND source_record_id = ?",
                (record.source, record.source_record_id),
            ).fetchone()
            if existing:
                changed = existing["content_hash"] != record.content_hash
                conn.execute(
                    """
                    UPDATE raw_records
                    SET payload_json = ?, content_hash = ?, fetched_at = ?, last_seen = ?, changed = ?
                    WHERE id = ?
                    """,
                    (
                        record.payload.model_dump_json() if hasattr(record.payload, "model_dump_json") else json.dumps(record.payload),
                        record.content_hash,
                        record.fetched_at.isoformat(),
                        now,
                        1 if changed else 0,
                        existing["id"],
                    ),
                )
                return int(existing["id"]), changed

            cursor = conn.execute(
                """
                INSERT INTO raw_records
                (source, source_record_id, payload_json, content_hash, fetched_at, first_seen, last_seen, changed)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    record.source,
                    record.source_record_id,
                    json.dumps(record.payload, sort_keys=True, default=str),
                    record.content_hash,
                    record.fetched_at.isoformat(),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

    def raw_records_needing_projects(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT r.*
                    FROM raw_records r
                    LEFT JOIN projects p ON p.raw_record_id = r.id
                    WHERE p.id IS NULL OR r.changed = 1
                    ORDER BY r.id
                    """
                )
            )

    def upsert_company(self, company: Company | None) -> int | None:
        if not company or not company.name.strip():
            return None
        now = utc_now()
        normalized = normalize_name(company.name)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, email, phone, website, license_number FROM companies WHERE normalized_name = ?",
                (normalized,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE companies
                    SET name = ?,
                        license_number = COALESCE(?, license_number),
                        website = COALESCE(?, website),
                        email = COALESCE(?, email),
                        phone = COALESCE(?, phone),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        company.name,
                        company.license_number,
                        company.website,
                        company.email,
                        company.phone,
                        now,
                        existing["id"],
                    ),
                )
                return int(existing["id"])

            cursor = conn.execute(
                """
                INSERT INTO companies
                (normalized_name, name, license_number, website, email, phone, first_seen, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    company.name,
                    company.license_number,
                    company.website,
                    company.email,
                    company.phone,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def upsert_project(self, project: Project) -> int:
        now = utc_now()
        company_id = self.upsert_company(project.company)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, content_hash FROM projects WHERE source = ? AND source_record_id = ?",
                (project.source, project.source_record_id),
            ).fetchone()
            values = (
                project.raw_record_id,
                company_id,
                project.source,
                project.source_record_id,
                project.permit_number,
                project.description,
                project.project_date,
                project.bid_deadline,
                project.address,
                project.city,
                project.county,
                project.source_url,
                project.category.value if project.category else None,
                project.confidence,
                project.status,
                project.content_hash,
                now,
            )
            if existing:
                conn.execute(
                    """
                    UPDATE projects
                    SET raw_record_id = ?, company_id = ?, source = ?, source_record_id = ?,
                        permit_number = ?, description = ?, project_date = ?, bid_deadline = ?,
                        address = ?, city = ?, county = ?, source_url = ?,
                        category = COALESCE(?, category),
                        confidence = COALESCE(?, confidence), status = ?, content_hash = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (*values, existing["id"]),
                )
                return int(existing["id"])

            cursor = conn.execute(
                """
                INSERT INTO projects
                (raw_record_id, company_id, source, source_record_id, permit_number, description,
                 project_date, bid_deadline, address, city, county, source_url, category, confidence,
                 status, content_hash, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values, now),
            )
            return int(cursor.lastrowid)

    def mark_raw_processed(self, raw_record_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE raw_records SET changed = 0 WHERE id = ?", (raw_record_id,))

    def set_project_classification(self, project_id: int, category: Category, confidence: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET category = ?, confidence = ? WHERE id = ?",
                (category.value, confidence, project_id),
            )

    def get_unclassified_projects(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM projects WHERE category IS NULL ORDER BY id"
        params: tuple[Any, ...] = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def get_projects_for_enrichment(self, category: str | None = None, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT p.*, c.name AS company_name, c.email, c.phone, c.website, c.license_number
            FROM projects p
            LEFT JOIN companies c ON c.id = p.company_id
            WHERE p.duplicate_of IS NULL AND (c.id IS NULL OR (c.email IS NULL AND c.phone IS NULL))
        """
        params: list[Any] = []
        if category:
            sql += " AND p.category = ?"
            params.append(category)
        sql += " ORDER BY p.id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(sql, tuple(params)))

    def record_enrichment_attempt(self, project_id: int, provider: str, status: str, detail: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO enrichment_attempts (project_id, provider, status, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, provider, status, detail, utc_now()),
            )

    def update_company_contact(self, company_id: int, email: str | None, phone: str | None, website: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE companies
                SET email = COALESCE(?, email), phone = COALESCE(?, phone),
                    website = COALESCE(?, website), updated_at = ?
                WHERE id = ?
                """,
                (email, phone, website, utc_now(), company_id),
            )

    def update_company_license_info(
        self,
        company_id: int,
        address: str | None,
        license_status: str | None,
        license_classification: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE companies
                SET address = COALESCE(?, address),
                    license_status = COALESCE(?, license_status),
                    license_classification = COALESCE(?, license_classification),
                    updated_at = ?
                WHERE id = ?
                """,
                (address, license_status, license_classification, utc_now(), company_id),
            )

    def set_watermark(self, source: str, watermark: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_watermarks (source, watermark, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET watermark = excluded.watermark, updated_at = excluded.updated_at
                """,
                (source, watermark, utc_now()),
            )

    def get_watermark(self, source: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT watermark FROM source_watermarks WHERE source = ?",
                (source,),
            ).fetchone()
            return str(row["watermark"]) if row else None

    def export_rows(self, category: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT p.*, c.name AS company_name, c.email, c.phone, c.website, c.license_number
            FROM projects p
            LEFT JOIN companies c ON c.id = p.company_id
            WHERE p.duplicate_of IS NULL
        """
        params: list[Any] = []
        if category:
            sql += " AND p.category = ?"
            params.append(category)
        sql += " ORDER BY p.category, p.project_date DESC, p.id DESC"
        with self.connect() as conn:
            return list(conn.execute(sql, tuple(params)))

    def projects_for_dedupe(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT id, source, description, address, company_id, first_seen
                    FROM projects
                    ORDER BY id
                    """
                )
            )

    def reset_duplicate_flags(self) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE projects SET duplicate_of = NULL")

    def mark_duplicates(self, pairs: list[tuple[int, int]]) -> None:
        """Set duplicate_of for each (project_id, canonical_id) pair."""
        with self.connect() as conn:
            conn.executemany(
                "UPDATE projects SET duplicate_of = ? WHERE id = ?",
                [(canonical_id, project_id) for project_id, canonical_id in pairs],
            )


def normalize_name(name: str) -> str:
    return " ".join(name.lower().replace("&", "and").split())
