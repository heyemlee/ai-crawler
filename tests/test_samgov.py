import json

import httpx
import pytest

from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.sources.samgov import SamGovOpportunitiesSource, _sam_date


OPPORTUNITY = {
    "noticeId": "abc123",
    "title": "Renovation of Civic Center HVAC",
    "solicitationNumber": "W912-26-R-0001",
    "fullParentPathName": "GENERAL SERVICES ADMINISTRATION.PUBLIC BUILDINGS SERVICE",
    "postedDate": "2026-05-15",
    "type": "Solicitation",
    "naicsCode": "236220",
    "responseDeadLine": "2026-06-15T17:00:00-07:00",
    "pointOfContact": [
        {"type": "primary", "fullName": "Jane Contracting Officer", "email": "jane.co@gsa.gov", "phone": "4155559876"},
    ],
    "placeOfPerformance": {"city": {"name": "San Francisco"}, "state": {"name": "California", "code": "CA"}},
    "uiLink": "https://sam.gov/opp/abc123/view",
}


class FakeSamClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(params or {})
        if (params or {}).get("offset", 0):
            return {"opportunitiesData": []}
        return {"totalRecords": 1, "opportunitiesData": [dict(OPPORTUNITY)]}


def _config() -> SourceConfig:
    return SourceConfig(
        type="samgov_opportunities",
        name="SAM.gov Construction",
        jurisdiction="California",
        county="Bay Area",
        naics_codes=["236220"],
        region="bay_area",
    )


def test_sam_date_formats_iso_to_us() -> None:
    assert _sam_date("2026-05-15") == "05/15/2026"
    assert _sam_date("2026-05-15T17:00:00-07:00") == "05/15/2026"


def test_sam_fetch_requires_api_key() -> None:
    source = SamGovOpportunitiesSource("samgov", _config(), FakeSamClient(), api_key=None)
    with pytest.raises(RuntimeError):
        list(source.fetch(since="2026-05-01"))


def test_sam_fetch_yields_records_and_passes_filters() -> None:
    client = FakeSamClient()
    source = SamGovOpportunitiesSource("samgov", _config(), client, api_key="test-key")

    records = list(source.fetch(since="2026-05-01"))

    assert len(records) == 1
    record = records[0]
    assert record.source_record_id == "abc123"
    assert record.payload["_source_url"] == "https://sam.gov/opp/abc123/view"
    # No watermark: SAM has no ascending sort, so the CLI keeps using the lookback window.
    assert source.latest_watermark is None
    first_call = client.calls[0]
    assert first_call["postedFrom"] == "05/01/2026"
    assert first_call["ncode"] == "236220"
    assert first_call["api_key"] == "test-key"
    # State filtering is client-side, not an API param (the API param is unreliable).
    assert "state" not in first_call


def test_sam_fetch_keeps_only_bay_area_place_of_performance() -> None:
    sf = dict(OPPORTUNITY, noticeId="sf-1")  # San Francisco
    moffett = dict(
        OPPORTUNITY,
        noticeId="moffett-1",
        # Source city name is garbled ("LINDA") but the ZIP is Bay Area (Moffett Field).
        placeOfPerformance={"city": {"name": "LINDA"}, "state": {"code": "CA"}, "zip": "94035"},
    )
    fresno = dict(
        OPPORTUNITY,
        noticeId="fresno-1",
        postedDate="2026-05-16",
        placeOfPerformance={"city": {"name": "Fresno"}, "state": {"code": "CA"}, "zip": "93701"},
    )
    nevada = dict(
        OPPORTUNITY,
        noticeId="nv-1",
        placeOfPerformance={"streetAddress": "3100 Craycroft Road, Las Vegas, NV 89086"},
    )

    class MultiClient:
        def get_json(self, url, params=None, **kwargs):
            if (params or {}).get("offset", 0):
                return {"opportunitiesData": []}
            return {"opportunitiesData": [sf, moffett, fresno, nevada]}

    source = SamGovOpportunitiesSource("samgov", _config(), MultiClient(), api_key="test-key")
    records = list(source.fetch(since="2026-05-01"))

    assert [r.source_record_id for r in records] == ["sf-1", "moffett-1"]


def test_sam_fetch_stops_gracefully_on_rate_limit() -> None:
    class RateLimitedClient:
        def get_json(self, url, params=None, **kwargs):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))

    source = SamGovOpportunitiesSource("samgov", _config(), RateLimitedClient(), api_key="test-key")
    records = list(source.fetch(since="2026-05-01"))

    assert records == []
    assert source.rate_limited is True


def test_sam_normalize_extracts_agency_and_poc_contact() -> None:
    config = _config()
    source = SamGovOpportunitiesSource("samgov", config, FakeSamClient(), api_key="test-key")
    record = list(source.fetch(since="2026-05-01"))[0]
    row = {
        "id": 1,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }

    project = normalize_raw_record(row, config)

    assert project.company is not None
    assert project.company.name == "PUBLIC BUILDINGS SERVICE"
    assert project.company.email == "jane.co@gsa.gov"
    assert project.company.phone == "(415) 555-9876"
    assert project.permit_number == "W912-26-R-0001"
    assert project.project_date == "2026-05-15"
    assert project.bid_deadline == "2026-06-15"
    assert project.address == "San Francisco, California"
    assert project.source_url == "https://sam.gov/opp/abc123/view"
    assert "Renovation of Civic Center HVAC" in project.description
