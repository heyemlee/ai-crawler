import json
from datetime import UTC, datetime

from bay_area_projectintel.config import SocrataFieldMap, SourceConfig
from bay_area_projectintel.pipeline.normalize import normalize_raw_record
from bay_area_projectintel.sources.arcgis import ArcGisPermitsSource

SERVICE = "https://example.arcgis.com/x/arcgis/rest/services/CampbellPermits_ActiveBuilding/FeatureServer"


def _epoch_ms(iso_date: str) -> int:
    return int(datetime.fromisoformat(iso_date).replace(tzinfo=UTC).timestamp() * 1000)


ISSUED_1 = _epoch_ms("2026-04-01")
ISSUED_2 = _epoch_ms("2026-04-02")

FEATURES = [
    {"attributes": {"OBJECTID": 1, "PermitID": "P1", "ApplicationNumber": "BLD-1",
                    "ProjectName": "MC LARNEY CONSTRUCTION", "ProjectDescription": "Office tenant improvement",
                    "Address": "46 S Central Ave", "City": "Campbell", "IssuedDate": ISSUED_1}},
    {"attributes": {"OBJECTID": 2, "PermitID": "P2", "ApplicationNumber": "BLD-2",
                    "ProjectName": "COBALT POWER SYSTEMS INC", "ProjectDescription": "Roof mounted PV system",
                    "Address": "700 Parkdale Dr", "City": "Campbell", "IssuedDate": ISSUED_2}},
]


class FakeArcGisClient:
    def __init__(self):
        self.calls: list[dict] = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(params or {})
        if (params or {}).get("resultOffset", 0):
            return {"features": []}
        return {"features": FEATURES, "exceededTransferLimit": False}


def _config() -> SourceConfig:
    return SourceConfig(
        type="arcgis_building_permits",
        name="Campbell Building Permits",
        jurisdiction="Campbell",
        county="Santa Clara",
        arcgis_service_url=SERVICE,
        arcgis_layer=0,
        date_field="IssuedDate",
        field_map=SocrataFieldMap(
            record_id="PermitID",
            permit_number="ApplicationNumber",
            description=["ProjectDescription", "WorkType"],
            address=["Address"],
            project_date=["IssuedDate"],
            city="City",
            company_name="ProjectName",
        ),
    )


def test_arcgis_fetch_flattens_attributes_and_converts_dates() -> None:
    client = FakeArcGisClient()
    source = ArcGisPermitsSource("campbell", _config(), client)

    records = list(source.fetch(since="2026-03-01"))

    assert [r.source_record_id for r in records] == ["P1", "P2"]
    # epoch ms converted to ISO date in the payload
    assert records[0].payload["IssuedDate"] == "2026-04-01"
    # watermark = max issued date seen
    assert source.latest_watermark == "2026-04-02"
    # date filter pushed into the ArcGIS where clause
    assert "IssuedDate >= DATE '2026-03-01'" in client.calls[0]["where"]


def test_arcgis_record_normalizes_to_project_with_company() -> None:
    client = FakeArcGisClient()
    source = ArcGisPermitsSource("campbell", _config(), client)
    record = list(source.fetch(since="2026-03-01"))[0]
    row = {
        "id": 1,
        "source": record.source,
        "source_record_id": record.source_record_id,
        "payload_json": json.dumps(record.payload),
    }

    project = normalize_raw_record(row, _config())

    assert project.company is not None
    assert project.company.name == "MC LARNEY CONSTRUCTION"
    assert project.city == "Campbell"
    assert project.project_date == "2026-04-01"
    assert project.permit_number == "BLD-1"
    assert "tenant improvement" in project.description.lower()
