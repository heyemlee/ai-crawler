import json

from bay_area_projectintel.config import SocrataFieldMap, SourceConfig
from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.sources.socrata import SocrataPermitsSource


PERMITS = [
    {
        "record_id": "rec-1",
        "permit_number": "2026-0001",
        "permit_type_definition": "alterations or repairs",
        "description": "Restaurant tenant improvement, new kitchen hood",
        "street_number": "100",
        "street_name": "Market",
        "street_suffix": "St",
        "issued_date": "2026-05-10T00:00:00.000",
        "data_loaded_at": "2026-05-20T00:00:00.000",
    }
]

CONTACTS = [
    {"permit_number": "2026-0001", "role": "Owner", "first_name": "Jane", "last_name": "Doe"},
    {
        "permit_number": "2026-0001",
        "role": "Contractor",
        "firm_name": "Acme Builders Inc",
        "license1": "492944",
        "phone": "4155551212",
    },
]


class FakeSocrataClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(url)
        if "3pee-9qhc" in url:
            return CONTACTS
        if (params or {}).get("$offset", 0):
            return []
        return PERMITS


def _config() -> SourceConfig:
    return SourceConfig(
        type="socrata_building_permits",
        domain="data.sfgov.org",
        dataset_id="i98e-djp9",
        contacts_dataset_id="3pee-9qhc",
        name="DataSF Building Permits",
        jurisdiction="San Francisco",
        county="San Francisco",
        date_field="data_loaded_at",
        source_url_template="https://data.sfgov.org/resource/i98e-djp9.json?permit_number={permit_number}",
    )


def test_socrata_fetch_joins_contacts_and_sets_watermark() -> None:
    config = _config()
    source = SocrataPermitsSource("datasf-building-permits", config, FakeSocrataClient())

    records = list(source.fetch(since="2026-05-01"))

    assert len(records) == 1
    record = records[0]
    assert record.source_record_id == "rec-1"
    assert record.payload["_contacts"]
    assert record.payload["_source_url"].endswith("permit_number=2026-0001")
    assert source.latest_watermark == "2026-05-20T00:00:00.000"


def test_socrata_normalize_prefers_contractor_over_owner() -> None:
    config = _config()
    source = SocrataPermitsSource("datasf-building-permits", config, FakeSocrataClient())
    record = list(source.fetch(since="2026-05-01"))[0]
    row = {
        "id": 1,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }

    project = normalize_raw_record(row, config)

    assert project.company is not None
    assert project.company.name == "Acme Builders Inc"
    assert project.company.license_number == "492944"
    assert project.company.phone == "(415) 555-1212"
    assert project.city == "San Francisco"
    assert project.project_date == "2026-05-10"


# A city with a different schema and the contractor on the permit record itself.
OTHER_CITY_PERMITS = [
    {
        "id_col": "P-77",
        "permit_no": "BLD-2026-77",
        "work_description": "Office tenant improvement, 2nd floor",
        "house_no": "200",
        "street": "Broadway",
        "issue_date": "2026-05-12T00:00:00",
        "applied_date": "2026-05-01T00:00:00",
        "contractor": "BuildRight Co",
        "contractor_lic": "1122753",
        "contractor_phone": "5105551234",
    }
]


class OtherCityClient:
    def get_json(self, url, params=None, **kwargs):
        if (params or {}).get("$offset", 0):
            return []
        return OTHER_CITY_PERMITS


def _other_city_config() -> SourceConfig:
    return SourceConfig(
        type="socrata_building_permits",
        domain="data.othercity.gov",
        dataset_id="abcd-1234",
        name="Other City Permits",
        jurisdiction="Other City",
        county="Alameda",
        date_field="issue_date",
        field_map=SocrataFieldMap(
            record_id="id_col",
            permit_number="permit_no",
            description=["work_description"],
            address=["house_no", "street"],
            project_date=["issue_date", "applied_date"],
            company_name="contractor",
            company_license="contractor_lic",
            company_phone="contractor_phone",
        ),
    )


def test_socrata_company_from_permit_record_when_no_contacts_dataset() -> None:
    config = _other_city_config()
    source = SocrataPermitsSource("othercity-permits", config, OtherCityClient())
    record = list(source.fetch(since="2026-05-01"))[0]
    row = {
        "id": 2,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }

    project = normalize_raw_record(row, config)

    assert record.source_record_id == "P-77"
    assert project.permit_number == "BLD-2026-77"
    assert project.description == "Office tenant improvement, 2nd floor"
    assert project.address == "200 Broadway"
    assert project.project_date == "2026-05-12"
    assert project.county == "Alameda"
    assert project.company is not None
    assert project.company.name == "BuildRight Co"
    assert project.company.license_number == "1122753"
    assert project.company.phone == "(510) 555-1234"
