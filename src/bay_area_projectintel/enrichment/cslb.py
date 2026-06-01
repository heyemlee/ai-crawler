from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

import httpx

from bay_area_projectintel.enrichment.base import EnrichmentResult


CONTRACTOR_LIST_URL = "https://www2.cslb.ca.gov/onlineservices/dataportal/ContractorList"
MASTER_CSV_EVENT_TARGET = "ctl00$MainContent$lbMasterCSV"


class CslbEnricher:
    provider = "cslb"

    def __init__(self, master_csv: Path, target_licenses: Iterable[str] | None = None):
        self.master_csv = master_csv
        self._index: dict[str, dict[str, str]] | None = None
        self._target_licenses = {
            normalized
            for value in (target_licenses or [])
            if (normalized := normalize_license(value))
        }

    def enrich(self, company_name: str | None, license_number: str | None) -> EnrichmentResult:
        license_key = normalize_license(license_number)
        if not license_key:
            return EnrichmentResult(self.provider, "skipped", detail="No numeric CSLB license number")
        if not self.master_csv.exists():
            return EnrichmentResult(
                self.provider,
                "pending",
                detail=f"CSLB master CSV not found at {self.master_csv}",
            )

        record = self._load_index().get(license_key)
        if not record:
            return EnrichmentResult(self.provider, "not_found", detail=f"License {license_key} not in CSLB master CSV")

        phone = clean_phone(record.get("BusinessPhone"))
        business_name = record.get("FullBusinessName") or record.get("BusinessName") or company_name
        license_info = dict(
            address=_mailing_address(record),
            license_status=_license_status(record),
            license_classification=record.get("Classifications(s)") or None,
        )
        if not phone:
            # No public phone, but the license matched — still surface the address /
            # status / classification so they get persisted for later use.
            return EnrichmentResult(
                self.provider,
                "not_found",
                detail=f"License {license_key} matched but has no public phone",
                **license_info,
            )

        return EnrichmentResult(
            self.provider,
            "updated",
            phone=phone,
            detail=f"Matched CSLB license {license_key}: {business_name}",
            **license_info,
        )

    def _load_index(self) -> dict[str, dict[str, str]]:
        if self._index is not None:
            return self._index

        index: dict[str, dict[str, str]] = {}
        with self.master_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                license_key = normalize_license(row.get("LicenseNo"))
                if not license_key:
                    continue
                if self._target_licenses and license_key not in self._target_licenses:
                    continue
                index[license_key] = row
        self._index = index
        return index


def _mailing_address(record: dict[str, str]) -> str | None:
    parts = [
        record.get("MailingAddress"),
        record.get("City"),
        record.get("State"),
        record.get("ZIPCode"),
    ]
    address = " ".join(str(part).strip() for part in parts if part and str(part).strip())
    return address or None


def _license_status(record: dict[str, str]) -> str | None:
    parts = [record.get("PrimaryStatus"), record.get("SecondaryStatus")]
    status = " / ".join(str(part).strip() for part in parts if part and str(part).strip())
    return status or None


def normalize_license(value: str | None) -> str | None:
    if not value:
        return None
    if re.search(r"[A-Za-z]", str(value)):
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits or len(digits) > 8:
        return None
    return digits.lstrip("0") or None


def clean_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return value.strip() or None


def download_master_csv(destination: Path, user_agent: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        page = client.get(CONTRACTOR_LIST_URL)
        page.raise_for_status()
        first_post = _hidden_fields(page.text)
        first_post["ctl00$MainContent$ddlStatus"] = "M"
        selected = client.post(CONTRACTOR_LIST_URL, data=first_post)
        selected.raise_for_status()

        download_post = _hidden_fields(selected.text)
        download_post["ctl00$MainContent$ddlStatus"] = "M"
        download_post["__EVENTTARGET"] = MASTER_CSV_EVENT_TARGET
        download_post["__EVENTARGUMENT"] = ""
        with client.stream("POST", CONTRACTOR_LIST_URL, data=download_post) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
    return destination


def _hidden_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        pattern = rf'name="{name}" id="{name}" value="([^"]*)"'
        match = re.search(pattern, html)
        fields[name] = match.group(1) if match else ""
    return fields
