"""Discover scanner unit tests with stubbed HTTP responses.

Covers field bucketing (contractor vs owner), Socrata catalog filtering,
ArcGIS FeatureServer probing, and the empty / failure paths.
"""

from __future__ import annotations

from bay_area_projectintel.sources.discover import (
    CityTarget,
    _bucket_fields,
    discover_cities,
    findings_to_dict,
    findings_to_markdown,
    probe_arcgis,
    probe_socrata,
)


class FakeClient:
    """Records calls and returns canned responses keyed by URL substring."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url, params=None, **kwargs):  # noqa: ANN001 - test stub
        self.calls.append((url, dict(params or {})))
        for key, value in self.responses.items():
            if key in url:
                if callable(value):
                    return value(url, params or {})
                return value
        raise AssertionError(f"unexpected url {url}")


# ---------- Field bucketing ----------


def test_bucket_fields_contractor_takes_precedence() -> None:
    contractor, owner = _bucket_fields(
        ["ContractorName", "ContractorLicense", "OwnerName", "ApplicantName", "Address"]
    )
    assert "ContractorName" in contractor
    assert "ContractorLicense" in contractor
    assert "OwnerName" in owner
    assert "ApplicantName" in owner
    assert "Address" not in contractor + owner


def test_bucket_fields_campbell_projectname_is_owner_not_contractor() -> None:
    # Campbell's `ProjectName` is the property owner / applicant, never the
    # contractor — discover must flag it as owner-shaped so it does not get
    # promoted as a candidate.
    contractor, owner = _bucket_fields(["ProjectName", "Address", "IssuedDate"])
    assert contractor == []
    assert owner == ["ProjectName"]


# ---------- Socrata probe ----------


def test_probe_socrata_returns_candidate_when_contractor_field_present() -> None:
    catalog = {
        "results": [
            {
                "resource": {
                    "id": "abcd-1234",
                    "name": "City Building Permits",
                    "description": "All issued building permits",
                },
                "metadata": {"domain": "data.example.org"},
            }
        ]
    }
    sample_row = [
        {
            "permit_number": "BLD-1",
            "contractor_name": "Acme Construction Inc",
            "issued_date": "2026-05-20T00:00:00.000",
        }
    ]
    latest = [{"issued_date": "2026-05-29T00:00:00.000"}]

    def sample_router(url, params):
        if params.get("$order"):
            return latest
        return sample_row

    client = FakeClient(
        {
            "api.us.socrata.com": catalog,
            "data.example.org/resource/abcd-1234.json": sample_router,
        }
    )

    findings = probe_socrata("Example", client, county="Test")

    assert len(findings) == 1
    finding = findings[0]
    assert finding.has_contractor is True
    assert finding.verdict() == "candidate"
    assert "contractor_name" in finding.contractor_fields
    assert finding.latest_date == "2026-05-29T00:00:00.000"
    assert finding.api_url == "https://data.example.org/resource/abcd-1234.json"


def test_probe_socrata_filters_out_non_permit_catalog_hits() -> None:
    # Sonoma's body-art / chemical datasets came back for "permit" queries — make
    # sure title/description filter trims them before we waste a sample call.
    catalog = {
        "results": [
            {
                "resource": {"id": "x1", "name": "Body Art Establishments", "description": ""},
                "metadata": {"domain": "data.example.org"},
            }
        ]
    }
    client = FakeClient({"api.us.socrata.com": catalog})

    findings = probe_socrata("Example", client)

    assert len(findings) == 1
    assert findings[0].title.startswith("(no permit-shaped")
    # only the catalog call — sample fetch must not have fired
    assert len(client.calls) == 1


def test_probe_socrata_drops_cross_state_noise_missing_city_name() -> None:
    # Real failure mode: querying "Saratoga" matched NY state datasets (sporting
    # licenses, fish hatcheries) by relevance — none mention Saratoga, CA. The
    # city-in-haystack filter must drop these.
    catalog = {
        "results": [
            {
                "resource": {
                    "id": "noise1",
                    "name": "Active Sporting License Issuing Agents",
                    "description": "statewide license agents",
                },
                "metadata": {"domain": "data.ny.gov"},
            }
        ]
    }
    client = FakeClient({"api.us.socrata.com": catalog})
    findings = probe_socrata("Saratoga", client)
    assert findings[0].title.startswith("(no permit-shaped")
    # Catalog hit but the filter dropped it before sampling
    assert len(client.calls) == 1


def test_probe_socrata_matches_city_across_space_variants() -> None:
    # "San Jose" should still match a domain like "data.sanjoseca.gov" even though
    # the domain drops the space.
    catalog = {
        "results": [
            {
                "resource": {"id": "x1", "name": "Building Permits", "description": "issued permits"},
                "metadata": {"domain": "data.sanjoseca.gov"},
            }
        ]
    }
    sample = [{"permit_number": "BLD-1", "contractor_name": "Acme", "issued_date": "2026-05-20"}]
    client = FakeClient({"api.us.socrata.com": catalog, "data.sanjoseca.gov": sample})

    findings = probe_socrata("San Jose", client)

    assert findings[0].has_contractor is True


def test_probe_socrata_handles_empty_catalog() -> None:
    client = FakeClient({"api.us.socrata.com": {"results": []}})
    findings = probe_socrata("Nowhere", client)
    assert len(findings) == 1
    assert findings[0].has_contractor is False
    assert "0 results" in findings[0].notes[0]


def test_probe_socrata_marks_owner_only_dataset() -> None:
    catalog = {
        "results": [
            {
                "resource": {"id": "x1", "name": "Construction Permits", "description": "issued permits"},
                "metadata": {"domain": "data.example.org"},
            }
        ]
    }
    sample = [{"permit_number": "BLD-1", "owner_name": "Jane Doe", "issued_date": "2026-05-20"}]
    client = FakeClient({"api.us.socrata.com": catalog, "data.example.org": sample})

    findings = probe_socrata("Example", client)

    assert findings[0].has_contractor is False
    assert findings[0].owner_fields == ["owner_name"]
    assert findings[0].verdict() == "owner-only"


# ---------- ArcGIS probe ----------


def test_probe_arcgis_returns_candidate_with_contractor_field() -> None:
    search = {
        "results": [
            {
                "title": "Example Building Permits",
                "snippet": "Construction permit data",
                "url": "https://services7.arcgis.com/X/arcgis/rest/services/Permits/FeatureServer",
            }
        ]
    }
    sample = {
        "features": [
            {
                "attributes": {
                    "OBJECTID": 1,
                    "ContractorName": "Acme Construction",
                    "ContractorLicense": "12345",
                    "IssuedDate": 1748563200000,  # 2025-05-30
                }
            }
        ]
    }
    latest = {"features": [{"attributes": {"IssuedDate": 1748563200000}}]}

    def router(url, params):
        if params.get("orderByFields"):
            return latest
        return sample

    client = FakeClient(
        {
            "arcgis.com/sharing/rest/search": search,
            "services7.arcgis.com": router,
        }
    )

    findings = probe_arcgis("Example", client)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.has_contractor is True
    assert "ContractorName" in finding.contractor_fields
    assert finding.api_url.endswith("/FeatureServer/0/query")
    assert finding.latest_date == "2025-05-30"


def test_probe_arcgis_flags_owner_only_like_campbell() -> None:
    # Real Campbell shape: ProjectName (= owner/applicant) and no contractor field.
    search = {
        "results": [
            {
                "title": "Campbell Permits",
                "snippet": "Active building permits",
                "url": "https://services7.arcgis.com/X/arcgis/rest/services/CampbellPermits/FeatureServer",
            }
        ]
    }
    sample = {
        "features": [
            {
                "attributes": {
                    "OBJECTID": 1,
                    "ProjectName": "O NEILL",
                    "Address": "100 Main",
                    "IssuedDate": 1748563200000,
                }
            }
        ]
    }
    client = FakeClient(
        {
            "arcgis.com/sharing/rest/search": search,
            "services7.arcgis.com": sample,
        }
    )

    findings = probe_arcgis("Campbell", client)

    assert findings[0].has_contractor is False
    assert findings[0].owner_fields == ["ProjectName"]
    assert findings[0].verdict() == "owner-only"


def test_probe_arcgis_drops_global_noise_missing_city_name() -> None:
    # Real failure mode: searching for "Mountain View" returned "DOC Recreational
    # Hunting Permit Areas" because "permit" matched. Both the city name and a
    # permit term must appear before we sample.
    search = {
        "results": [
            {
                "title": "DOC Recreational Hunting Permit Areas",
                "snippet": "national hunting permit boundaries",
                "url": "https://services1.arcgis.com/X/arcgis/rest/services/Hunting/FeatureServer",
            }
        ]
    }
    client = FakeClient({"arcgis.com/sharing/rest/search": search})
    findings = probe_arcgis("Mountain View", client)
    assert findings[0].title.startswith("(no permit-shaped")
    # Only the search call — no sample fetch wasted.
    assert len(client.calls) == 1


def test_probe_arcgis_ignores_non_feature_servers() -> None:
    search = {
        "results": [
            {
                "title": "City Permits Map",
                "snippet": "permit viewer",
                "url": "https://example.com/permits-map",  # not a FeatureServer
            }
        ]
    }
    client = FakeClient({"arcgis.com/sharing/rest/search": search})
    findings = probe_arcgis("Example", client)
    assert findings[0].title.startswith("(no permit-shaped")


# ---------- Orchestration + report formatting ----------


def test_discover_cities_runs_both_probes_per_city() -> None:
    client = FakeClient(
        {
            "api.us.socrata.com": {"results": []},
            "arcgis.com/sharing/rest/search": {"results": []},
        }
    )

    findings = discover_cities([CityTarget("San Jose", "Santa Clara")], client)

    kinds = {f.source_kind for f in findings}
    assert kinds == {"socrata", "arcgis"}


def test_findings_to_markdown_groups_by_city_and_highlights_candidates() -> None:
    client = FakeClient(
        {
            "api.us.socrata.com": {"results": []},
            "arcgis.com/sharing/rest/search": {"results": []},
        }
    )
    findings = discover_cities([CityTarget("San Jose", "Santa Clara")], client)
    md = findings_to_markdown(findings)
    assert "## San Jose (Santa Clara)" in md
    assert "No contractor-shaped dataset" in md


def test_findings_to_dict_is_json_serializable_shape() -> None:
    client = FakeClient(
        {"api.us.socrata.com": {"results": []}, "arcgis.com/sharing/rest/search": {"results": []}}
    )
    findings = discover_cities([CityTarget("San Jose")], client)
    data = findings_to_dict(findings)
    assert all("verdict" in row for row in data)
    assert all("has_contractor" in row for row in data)
