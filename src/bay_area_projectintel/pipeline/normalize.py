from __future__ import annotations

import json
import re
from typing import Any

from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.enrichment.cslb import clean_phone
from bay_area_projectintel.models import Company, Project


CONTRACTOR_ROLES = ("contractor", "subcontractor", "authorized agent", "architect", "engineer")
OWNER_ROLE_HINTS = ("owner", "applicant", "tenant")


def normalize_raw_record(row: Any, source_config: SourceConfig) -> Project:
    if source_config.type == "samgov_opportunities":
        return _normalize_samgov(row, source_config)
    return _normalize_socrata(row, source_config)


def _normalize_socrata(row: Any, source_config: SourceConfig) -> Project:
    payload = json.loads(row["payload_json"])
    field_map = source_config.field_map
    contacts = payload.get("_contacts") or []
    company = _company_from_contacts(contacts) if contacts else _company_from_record(payload, field_map)
    address = _address(payload, field_map.address)
    permit_number = payload.get(field_map.permit_number) if field_map.permit_number else None
    city = source_config.jurisdiction
    if field_map.city:
        city = _clean_company_name(str(payload.get(field_map.city) or "")).title() or source_config.jurisdiction

    description = _first_present(payload, *field_map.description) or ""
    description = description.strip()
    if not description:
        description = f"Permit at {address or 'unknown address'}"

    project_date = _date_only(_first_present(payload, *field_map.project_date))
    project_payload = {
        "source": row["source"],
        "source_record_id": row["source_record_id"],
        "permit_number": permit_number,
        "description": description,
        "project_date": project_date,
        "address": address,
        "city": city,
        "county": source_config.county,
        "company": company.model_dump() if company else None,
    }

    return Project(
        raw_record_id=int(row["id"]),
        source=row["source"],
        source_record_id=row["source_record_id"],
        permit_number=permit_number,
        description=description,
        project_date=project_date,
        address=address,
        city=city,
        county=source_config.county,
        source_url=payload.get("_source_url"),
        company=company,
        content_hash=stable_hash(project_payload),
    )


def _company_from_record(payload: dict[str, Any], field_map: Any) -> Company | None:
    """Build the contractor company from fields on the permit record (no contacts dataset)."""
    if not field_map.company_name:
        return None
    name = _clean_company_name(str(payload.get(field_map.company_name) or ""))
    if not name or name.lower() in {"owner", "none", "n/a"}:
        return None
    license_number = None
    if field_map.company_license:
        license_number = _clean_license(_first_present(payload, field_map.company_license))
    email = None
    if field_map.company_email:
        raw_email = payload.get(field_map.company_email)
        if raw_email:
            match = EMAIL_RE.search(str(raw_email))
            email = match.group(0) if match else None
    phone = None
    if field_map.company_phone:
        raw_phone = payload.get(field_map.company_phone)
        if raw_phone:
            match = PHONE_RE.search(str(raw_phone)) or BARE_PHONE_RE.search(str(raw_phone))
            phone = clean_phone(match.group(0)) if match else None
    return Company(name=name, license_number=license_number, email=email, phone=phone)


def _normalize_samgov(row: Any, source_config: SourceConfig) -> Project:
    payload = json.loads(row["payload_json"])
    company = _company_from_poc(payload.get("_contacts") or [], payload)
    address = _samgov_place(payload)
    # Use the real place-of-performance city; the config county is only a scope
    # label, so do not stamp it onto records whose true county we cannot derive.
    city = _samgov_city(payload) or source_config.jurisdiction
    title = str(payload.get("title") or "").strip()
    notice_type = str(payload.get("type") or "").strip()
    description = title or notice_type or "SAM.gov opportunity"
    if notice_type and title:
        description = f"[{notice_type}] {title}"
    permit_number = _first_present(payload, "solicitationNumber", "noticeId")
    project_date = _date_only(_first_present(payload, "postedDate"))
    bid_deadline = _date_only(_first_present(payload, "responseDeadLine"))

    project_payload = {
        "source": row["source"],
        "source_record_id": row["source_record_id"],
        "permit_number": permit_number,
        "description": description,
        "project_date": project_date,
        "bid_deadline": bid_deadline,
        "address": address,
        "city": city,
        "county": None,
        "company": company.model_dump() if company else None,
    }

    return Project(
        raw_record_id=int(row["id"]),
        source=row["source"],
        source_record_id=row["source_record_id"],
        permit_number=permit_number,
        description=description,
        project_date=project_date,
        bid_deadline=bid_deadline,
        address=address,
        city=city,
        county=None,
        source_url=payload.get("_source_url"),
        company=company,
        content_hash=stable_hash(project_payload),
    )


def _company_from_contacts(contacts: list[dict[str, Any]]) -> Company | None:
    """Pick the best permit contact: prefer contractors and firm entities; skip individual owners."""
    if not contacts:
        return None
    scored: list[tuple[tuple[int, int, int], Company]] = []
    for contact in contacts:
        firm = _first_present(contact, "firm_name", "company_name")
        person = " ".join(
            part
            for part in (str(contact.get("first_name") or "").strip(), str(contact.get("last_name") or "").strip())
            if part
        )
        name = _clean_company_name(str(firm or person or ""))
        if not name or name.lower() in {"owner owner", "owner", "none", "n/a"}:
            continue

        role = str(contact.get("role") or "").lower()
        is_owner = any(hint in role for hint in OWNER_ROLE_HINTS)
        license_number = _clean_license(_first_present(contact, "license1", "license2"))
        # Skip individual owners (no firm, no license) — we want the contractor/company.
        if is_owner and not firm and not license_number:
            continue

        key = (
            _contact_rank(role),
            0 if firm else 1,
            0 if license_number else 1,
        )
        scored.append(
            (
                key,
                Company(
                    name=name,
                    license_number=license_number,
                    email=_find_email(contact),
                    phone=_find_phone(contact),
                ),
            )
        )

    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _company_from_poc(contacts: list[dict[str, Any]], payload: dict[str, Any]) -> Company | None:
    """Path A: SAM.gov RFPs carry an issuing-agency point of contact (name/email/phone)."""
    org_name = _samgov_org_name(payload)
    ranked = sorted(contacts, key=lambda c: 0 if str(c.get("type") or "").lower() == "primary" else 1)
    for contact in ranked:
        email = _find_email(contact)
        phone = _find_phone(contact)
        if not (email or phone):
            continue
        contact_name = _clean_company_name(str(contact.get("fullName") or ""))
        name = org_name or contact_name
        if not name:
            continue
        return Company(name=name, email=email, phone=phone)
    # No reachable POC; still surface the agency so it lands in Pending rather than vanishing.
    if org_name:
        return Company(name=org_name)
    return None


def _samgov_org_name(payload: dict[str, Any]) -> str | None:
    full = payload.get("fullParentPathName")
    if isinstance(full, str) and full.strip():
        return _clean_company_name(full.split(".")[-1])
    for key in ("organizationName", "subTier", "department", "office"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_company_name(value)
    return None


def _samgov_city(payload: dict[str, Any]) -> str | None:
    place = payload.get("placeOfPerformance")
    if isinstance(place, dict) and isinstance(place.get("city"), dict):
        name = (place["city"] or {}).get("name")
        if name:
            return _clean_company_name(str(name)).title()
    return None


def _samgov_place(payload: dict[str, Any]) -> str | None:
    place = payload.get("placeOfPerformance")
    if not isinstance(place, dict):
        return None
    city = (place.get("city") or {}).get("name") if isinstance(place.get("city"), dict) else None
    state = (place.get("state") or {}).get("name") if isinstance(place.get("state"), dict) else None
    if not state and isinstance(place.get("state"), dict):
        state = (place.get("state") or {}).get("code")
    parts = [part for part in (city, state) if part]
    return ", ".join(parts) or None


def _contact_rank(role: str) -> int:
    for index, candidate in enumerate(CONTRACTOR_ROLES):
        if candidate in role:
            return index
    return len(CONTRACTOR_ROLES)


def _address(payload: dict[str, Any], fields: list[str]) -> str | None:
    parts = [payload.get(field) for field in fields]
    value = " ".join(str(part).strip() for part in parts if part)
    return value or None


def _first_present(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _date_only(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("T", 1)[0]


def _clean_company_name(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def _clean_license(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.upper() in {"OWN", "OWNER", "NONE", "000000", "0000000"}:
        return None
    return value


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")
BARE_PHONE_RE = re.compile(r"\b1?\d{10}\b")


def _find_email(contact: dict[str, Any]) -> str | None:
    # Prefer the dedicated field, then fall back to scanning all values.
    for text in (contact.get("email"), " ".join(str(v) for v in contact.values() if v)):
        if not text:
            continue
        match = EMAIL_RE.search(str(text))
        if match:
            return match.group(0)
    return None


def _find_phone(contact: dict[str, Any]) -> str | None:
    # Prefer the dedicated field (which may carry an extension), then scan all values.
    for text in (contact.get("phone"), " ".join(str(v) for v in contact.values() if v)):
        if not text:
            continue
        match = PHONE_RE.search(str(text)) or BARE_PHONE_RE.search(str(text))
        if match:
            return clean_phone(match.group(0))
    return None
