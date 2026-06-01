"""Rerunnable scanner: probe Socrata catalog + ArcGIS for city permit datasets.

Goal: for a list of cities, surface which ones expose a public permits/construction
dataset and — critically — whether each candidate carries a contractor field
(satisfies the contact hard-requirement) or only an owner/applicant.

Two probes, both polite (cached + throttled by PoliteHttpClient):
  * Socrata global catalog at api.us.socrata.com (covers all Socrata domains).
  * ArcGIS Online search at arcgis.com/sharing/rest/search (covers public FeatureServers).

For each candidate dataset we fetch a 1-row sample so we can inspect actual field
names (not just metadata blurbs). Field names are bucketed into contractor-like
vs owner-like using conservative keyword lists; both buckets are reported so a
human can sanity-check before adding the source to sources.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from bay_area_projectintel.compliance.politeness import PoliteHttpClient


# Conservative: token must look like a contractor/licensee field, not the owner/applicant
# (Campbell's ProjectName, Sonoma's lack of contractor — both fail the contact gate).
CONTRACTOR_KEYWORDS = (
    "contractor",
    "contracting",
    "licensee",
    "license_no",
    "license_number",
    "license_num",
    "licensenumber",
    "firm_name",
    "business_name",
    "company_name",
    "contractorname",
    "contractor_name",
    "primary_contractor",
)
OWNER_KEYWORDS = (
    "owner",
    "owner_name",
    "applicant",
    "applicant_name",
    "applicant_first",
    "applicant_last",
    "project_name",  # Campbell case: this is the owner/applicant, not the contractor
    "homeowner",
)
PERMIT_QUERY_TERMS = ("building permit", "permit", "construction permit")


@dataclass
class DiscoveryFinding:
    """One probed dataset for one city.

    `has_contractor` is the gate the hard-requirement cares about; everything else
    is supporting evidence so a human can decide whether to add the source.
    """

    city: str
    county: str | None
    source_kind: str  # "socrata" | "arcgis"
    title: str
    dataset_url: str
    api_url: str | None
    domain: str | None
    has_contractor: bool
    contractor_fields: list[str] = field(default_factory=list)
    owner_fields: list[str] = field(default_factory=list)
    sample_field_count: int = 0
    latest_date: str | None = None
    notes: list[str] = field(default_factory=list)

    def verdict(self) -> str:
        if self.has_contractor:
            return "candidate"
        if self.owner_fields and not self.contractor_fields:
            return "owner-only"
        return "no-contact-fields"


# ---------- Field bucketing ----------


def _city_in_haystack(city: str, haystack: str) -> bool:
    """Match city across spacing variants: 'San Jose' ↔ 'sanjose' (domains drop spaces)."""
    lower = city.lower()
    return lower in haystack or lower.replace(" ", "") in haystack.replace(" ", "")


def _bucket_fields(field_names: Iterable[str]) -> tuple[list[str], list[str]]:
    contractor: list[str] = []
    owner: list[str] = []
    for name in field_names:
        # Normalize so `ProjectName` and `project_name` match the same keyword set.
        normalized = name.lower().replace("_", "").replace("-", "")
        contractor_keys = [k.replace("_", "") for k in CONTRACTOR_KEYWORDS]
        owner_keys = [k.replace("_", "") for k in OWNER_KEYWORDS]
        if any(keyword in normalized for keyword in contractor_keys):
            contractor.append(name)
        elif any(keyword in normalized for keyword in owner_keys):
            owner.append(name)
    return contractor, owner


# ---------- Socrata probe ----------

SOCRATA_CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"


def probe_socrata(
    city: str,
    client: PoliteHttpClient,
    *,
    county: str | None = None,
    max_candidates: int = 5,
) -> list[DiscoveryFinding]:
    """Hit the global Socrata catalog for `{city} permit` and sample the top hits.

    The catalog returns datasets across every Socrata domain, so this works even
    when we don't know the city's open-data subdomain in advance.
    """
    findings: list[DiscoveryFinding] = []
    query = f'"{city}" permit'
    try:
        catalog = client.get_json(
            SOCRATA_CATALOG_URL,
            params={"q": query, "limit": max_candidates, "only": "dataset"},
            check_robots=False,
        )
    except Exception as exc:
        return [
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="socrata",
                title="(catalog query failed)",
                dataset_url=SOCRATA_CATALOG_URL,
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=[f"catalog error: {exc}"],
            )
        ]

    results = (catalog or {}).get("results") or [] if isinstance(catalog, dict) else []
    if not results:
        return [
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="socrata",
                title="(no socrata catalog hits)",
                dataset_url=f"{SOCRATA_CATALOG_URL}?q={query}",
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=["socrata catalog returned 0 results"],
            )
        ]

    for entry in results:
        resource = entry.get("resource") or {}
        metadata = entry.get("metadata") or {}
        dataset_id = resource.get("id")
        title = resource.get("name") or "(untitled)"
        description = (resource.get("description") or "").strip()
        domain = metadata.get("domain")
        if not dataset_id or not domain:
            continue

        # Only investigate datasets whose title/description mentions permits or construction
        # (the Socrata search is loose; this filter trims body-art / chemical / GIS clutter).
        # Also require the city name in title/description/domain — the q= search returns
        # cross-state matches by relevance (a "Saratoga" query happily returns NY state
        # datasets that have no Saratoga, CA content).
        haystack = f"{title} {description} {domain}".lower()
        if not any(term in haystack for term in PERMIT_QUERY_TERMS):
            continue
        if not _city_in_haystack(city, haystack):
            continue

        api_url = f"https://{domain}/resource/{dataset_id}.json"
        dataset_url = f"https://{domain}/d/{dataset_id}"
        finding = DiscoveryFinding(
            city=city,
            county=county,
            source_kind="socrata",
            title=title,
            dataset_url=dataset_url,
            api_url=api_url,
            domain=domain,
            has_contractor=False,
        )

        sample = _safe_sample(client, api_url, params={"$limit": 1})
        if sample is None:
            finding.notes.append("sample fetch failed")
            findings.append(finding)
            continue
        if not sample:
            finding.notes.append("dataset is empty")
            findings.append(finding)
            continue

        row = sample[0]
        finding.sample_field_count = len(row)
        contractor, owner = _bucket_fields(row.keys())
        finding.contractor_fields = contractor
        finding.owner_fields = owner
        finding.has_contractor = bool(contractor)
        finding.latest_date = _socrata_latest_date(client, api_url, list(row.keys()))
        findings.append(finding)

    if not findings:
        findings.append(
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="socrata",
                title="(no permit-shaped catalog hits)",
                dataset_url=f"{SOCRATA_CATALOG_URL}?q={query}",
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=["catalog hits but none match permit/construction"],
            )
        )
    return findings


# Common date column names; we ask Socrata to sort desc by each and keep the newest hit.
SOCRATA_DATE_FIELDS = (
    "issued_date",
    "issue_date",
    "permit_issued_date",
    "filed_date",
    "permit_creation_date",
    "data_loaded_at",
    "received_date",
    "most_recent_issued_received_date",
    "application_date",
)


def _socrata_latest_date(client: PoliteHttpClient, api_url: str, field_names: list[str]) -> str | None:
    candidates = [name for name in SOCRATA_DATE_FIELDS if name in field_names]
    for column in candidates:
        try:
            rows = client.get_json(
                api_url,
                params={"$limit": 1, "$order": f"{column} DESC", "$select": column},
                check_robots=False,
            )
        except Exception:
            continue
        if isinstance(rows, list) and rows:
            value = rows[0].get(column)
            if isinstance(value, str):
                return value
    return None


# ---------- ArcGIS probe ----------

ARCGIS_SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"


def probe_arcgis(
    city: str,
    client: PoliteHttpClient,
    *,
    county: str | None = None,
    max_candidates: int = 5,
) -> list[DiscoveryFinding]:
    """Search ArcGIS Online for FeatureServers matching `{city} permit`."""
    findings: list[DiscoveryFinding] = []
    query = f'"{city}" permit (type:"Feature Service")'
    try:
        result = client.get_json(
            ARCGIS_SEARCH_URL,
            params={"q": query, "f": "json", "num": max_candidates},
            check_robots=False,
        )
    except Exception as exc:
        return [
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="arcgis",
                title="(search query failed)",
                dataset_url=ARCGIS_SEARCH_URL,
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=[f"search error: {exc}"],
            )
        ]

    items = (result or {}).get("results") or [] if isinstance(result, dict) else []
    if not items:
        return [
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="arcgis",
                title="(no arcgis search hits)",
                dataset_url=f"{ARCGIS_SEARCH_URL}?q={query}",
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=["arcgis search returned 0 results"],
            )
        ]

    for item in items:
        title = item.get("title") or "(untitled)"
        url = item.get("url")
        if not url or "FeatureServer" not in url:
            continue
        snippet = (item.get("snippet") or "").lower()
        haystack = f"{title.lower()} {snippet}"
        # Both city name and a permit term must appear — otherwise ArcGIS search
        # returns global noise like "DOC Recreational Hunting Permit Areas"
        # whenever the city name happens to be a common phrase ("Mountain View").
        if not _city_in_haystack(city, haystack):
            continue
        if not any(term in haystack for term in PERMIT_QUERY_TERMS):
            continue

        layer_url = _first_arcgis_layer_url(url)
        api_url = f"{layer_url}/query" if layer_url else None
        finding = DiscoveryFinding(
            city=city,
            county=county,
            source_kind="arcgis",
            title=title,
            dataset_url=url,
            api_url=api_url,
            domain=urlparse(url).netloc,
            has_contractor=False,
        )

        if not api_url:
            finding.notes.append("could not derive layer URL")
            findings.append(finding)
            continue

        sample = _safe_arcgis_sample(client, api_url)
        if sample is None:
            finding.notes.append("sample fetch failed")
            findings.append(finding)
            continue
        if not sample:
            finding.notes.append("dataset is empty")
            findings.append(finding)
            continue

        row = sample[0]
        finding.sample_field_count = len(row)
        contractor, owner = _bucket_fields(row.keys())
        finding.contractor_fields = contractor
        finding.owner_fields = owner
        finding.has_contractor = bool(contractor)
        finding.latest_date = _arcgis_latest_date(client, api_url, list(row.keys()))
        findings.append(finding)

    if not findings:
        findings.append(
            DiscoveryFinding(
                city=city,
                county=county,
                source_kind="arcgis",
                title="(no permit-shaped FeatureServer hits)",
                dataset_url=f"{ARCGIS_SEARCH_URL}?q={query}",
                api_url=None,
                domain=None,
                has_contractor=False,
                notes=["search hits but none match permit/construction"],
            )
        )
    return findings


def _first_arcgis_layer_url(service_url: str) -> str | None:
    # `.../FeatureServer` or `.../FeatureServer/0` — normalize to layer 0 endpoint.
    cleaned = service_url.rstrip("/")
    if re.search(r"/FeatureServer/\d+$", cleaned):
        return cleaned
    if cleaned.endswith("/FeatureServer"):
        return f"{cleaned}/0"
    return None


def _safe_arcgis_sample(client: PoliteHttpClient, api_url: str) -> list[dict[str, Any]] | None:
    try:
        data = client.get_json(
            api_url,
            params={"where": "1=1", "outFields": "*", "resultRecordCount": 1, "f": "json"},
            check_robots=False,
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    features = data.get("features") or []
    return [dict(feature.get("attributes") or {}) for feature in features]


ARCGIS_DATE_FIELDS = (
    "IssuedDate",
    "IssueDate",
    "Issued",
    "PermitIssueDate",
    "FiledDate",
    "ApplicationDate",
    "ApplyDate",
    "CreatedDate",
)


def _arcgis_latest_date(client: PoliteHttpClient, api_url: str, field_names: list[str]) -> str | None:
    candidates = [name for name in ARCGIS_DATE_FIELDS if name in field_names]
    for column in candidates:
        try:
            data = client.get_json(
                api_url,
                params={
                    "where": f"{column} IS NOT NULL",
                    "outFields": column,
                    "orderByFields": f"{column} DESC",
                    "resultRecordCount": 1,
                    "f": "json",
                },
                check_robots=False,
            )
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        features = data.get("features") or []
        if features:
            value = (features[0].get("attributes") or {}).get(column)
            if isinstance(value, (int, float)):
                # ArcGIS dates are epoch ms; convert lazily here so the caller gets ISO.
                from datetime import UTC, datetime

                return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()
            if isinstance(value, str):
                return value
    return None


# ---------- Sample helper for Socrata ----------


def _safe_sample(
    client: PoliteHttpClient, url: str, params: dict[str, object]
) -> list[dict[str, Any]] | None:
    try:
        data = client.get_json(url, params=params, check_robots=False)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


# ---------- Top-level orchestration ----------


@dataclass
class CityTarget:
    name: str
    county: str | None = None


def discover_cities(
    cities: list[CityTarget],
    client: PoliteHttpClient,
    *,
    max_candidates: int = 5,
) -> list[DiscoveryFinding]:
    findings: list[DiscoveryFinding] = []
    for target in cities:
        findings.extend(
            probe_socrata(target.name, client, county=target.county, max_candidates=max_candidates)
        )
        findings.extend(
            probe_arcgis(target.name, client, county=target.county, max_candidates=max_candidates)
        )
    return findings


# ---------- Report formatting ----------


def findings_to_markdown(findings: list[DiscoveryFinding]) -> str:
    lines = ["# Source Discovery Report\n"]
    grouped: dict[str, list[DiscoveryFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.city, []).append(finding)

    for city, items in grouped.items():
        county = items[0].county
        header = f"## {city}" + (f" ({county})" if county else "")
        lines.append(header)
        candidates = [f for f in items if f.has_contractor]
        if candidates:
            lines.append("**Has contractor-shaped fields — promote to sources.yaml after manual review.**\n")
        else:
            lines.append("_No contractor-shaped dataset found._\n")
        for item in items:
            verdict = item.verdict()
            lines.append(f"- **{item.source_kind}** · {verdict} · {item.title}")
            if item.api_url:
                lines.append(f"  - API: {item.api_url}")
            if item.contractor_fields:
                lines.append(f"  - contractor fields: {', '.join(item.contractor_fields)}")
            if item.owner_fields:
                lines.append(f"  - owner/applicant fields: {', '.join(item.owner_fields)}")
            if item.latest_date:
                lines.append(f"  - latest record date: {item.latest_date}")
            if item.notes:
                lines.append(f"  - notes: {'; '.join(item.notes)}")
        lines.append("")
    return "\n".join(lines)


def findings_to_dict(findings: list[DiscoveryFinding]) -> list[dict[str, Any]]:
    return [
        {
            "city": f.city,
            "county": f.county,
            "source_kind": f.source_kind,
            "title": f.title,
            "dataset_url": f.dataset_url,
            "api_url": f.api_url,
            "domain": f.domain,
            "has_contractor": f.has_contractor,
            "contractor_fields": f.contractor_fields,
            "owner_fields": f.owner_fields,
            "sample_field_count": f.sample_field_count,
            "latest_date": f.latest_date,
            "verdict": f.verdict(),
            "notes": f.notes,
        }
        for f in findings
    ]
