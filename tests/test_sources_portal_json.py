import json

from bay_area_projectintel.config import SocrataFieldMap, SourceConfig
from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.sources import build_source
from bay_area_projectintel.sources.portal_json import AccelaPermitsSource, EnerGovPermitsSource


ACCELA_ROWS = [
    {
        "recordId": "REC-1",
        "recordNumber": "BLD2026-001",
        "description": "Restaurant tenant improvement",
        "address": "100 Market St",
        "openedDate": "2026-05-20T00:00:00",
        "contractorName": "Acme Builders Inc",
        "contractorLicense": "492944",
        "contractorPhone": "4085551212",
    }
]


ENERGOV_ROWS = [
    {
        "permitId": "EG-1",
        "permitNumber": "B-2026-10",
        "workDescription": "Office lab remodel",
        "siteAddress": "200 Mathilda Ave",
        "issueDate": "2026-05-22",
        "contractorCompany": "BuildRight Co",
        "contractorEmail": "permits@buildright.example",
    }
]


class FakePortalClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append({"url": url, "params": params or {}, "kwargs": kwargs})
        if (params or {}).get("offset", 0) or (params or {}).get("page", 1) > 1:
            return {"result": {"records": []}}
        return self.response


def _accela_config() -> SourceConfig:
    return SourceConfig(
        type="accela_building_permits",
        name="San Jose Accela Permits",
        jurisdiction="San Jose",
        county="Santa Clara",
        search_url="https://permits.example.gov/api/records/search",
        detail_url_template="https://permits.example.gov/records/{recordNumber}",
        record_path="result.records",
        since_param="openedAfter",
        limit_param="limit",
        offset_param="offset",
        page_size=100,
        field_map=SocrataFieldMap(
            record_id="recordId",
            permit_number="recordNumber",
            description=["description"],
            address=["address"],
            project_date=["openedDate"],
            company_name="contractorName",
            company_license="contractorLicense",
            company_phone="contractorPhone",
        ),
    )


def _energov_config() -> SourceConfig:
    return SourceConfig(
        type="energov_building_permits",
        name="Sunnyvale EnerGov Permits",
        jurisdiction="Sunnyvale",
        county="Santa Clara",
        search_url="https://energov.example.gov/selfservice/api/permits/search",
        detail_url_template="https://energov.example.gov/selfservice#/permit/{permitNumber}",
        record_path="data",
        since_param="issuedAfter",
        limit_param="take",
        page_param="page",
        page_size=50,
        field_map=SocrataFieldMap(
            record_id="permitId",
            permit_number="permitNumber",
            description=["workDescription"],
            address=["siteAddress"],
            project_date=["issueDate"],
            company_name="contractorCompany",
            company_email="contractorEmail",
        ),
    )


def test_accela_json_source_fetches_and_normalizes() -> None:
    client = FakePortalClient({"result": {"records": ACCELA_ROWS}})
    config = _accela_config()
    source = AccelaPermitsSource("san-jose-accela", config, client)

    record = list(source.fetch(since="2026-03-01"))[0]

    assert record.source_record_id == "REC-1"
    assert record.payload["_source_url"].endswith("/records/BLD2026-001")
    assert client.calls[0]["params"]["openedAfter"] == "2026-03-01"
    assert client.calls[0]["params"]["limit"] == 100
    assert client.calls[0]["params"]["offset"] == 0
    assert client.calls[0]["kwargs"]["check_robots"] is False
    assert source.latest_watermark == "2026-05-20T00:00:00"

    project = normalize_raw_record(
        {"id": 1, "source": record.source, "source_record_id": record.source_record_id, "payload_json": json.dumps(record.payload)},
        config,
    )
    assert project.city == "San Jose"
    assert project.project_date == "2026-05-20"
    assert project.permit_number == "BLD2026-001"
    assert project.company is not None
    assert project.company.name == "Acme Builders Inc"
    assert project.company.license_number == "492944"
    assert project.company.phone == "(408) 555-1212"


def test_energov_json_source_fetches_and_normalizes() -> None:
    client = FakePortalClient({"data": ENERGOV_ROWS})
    config = _energov_config()
    source = EnerGovPermitsSource("sunnyvale-energov", config, client)

    record = list(source.fetch(since="2026-03-01"))[0]

    assert record.source_record_id == "EG-1"
    assert client.calls[0]["params"]["issuedAfter"] == "2026-03-01"
    assert client.calls[0]["params"]["take"] == 50
    assert client.calls[0]["params"]["page"] == 1
    assert source.latest_watermark == "2026-05-22"

    project = normalize_raw_record(
        {"id": 1, "source": record.source, "source_record_id": record.source_record_id, "payload_json": json.dumps(record.payload)},
        config,
    )
    assert project.city == "Sunnyvale"
    assert project.description == "Office lab remodel"
    assert project.company is not None
    assert project.company.email == "permits@buildright.example"


def test_source_factory_builds_accela_and_energov_sources() -> None:
    assert isinstance(build_source("accela", _accela_config(), FakePortalClient({})), AccelaPermitsSource)
    assert isinstance(build_source("energov", _energov_config(), FakePortalClient({})), EnerGovPermitsSource)
