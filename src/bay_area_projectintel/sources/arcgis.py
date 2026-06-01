from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import SourceConfig
from bay_area_projectintel.db import stable_hash
from bay_area_projectintel.models import RawRecord

ARCGIS_MAX_PAGE = 2000


class ArcGisPermitsSource:
    """Generic ArcGIS FeatureServer permits adapter, driven by SourceConfig.field_map.

    Adding an ArcGIS city is config-only: set arcgis_service_url / arcgis_layer /
    date_field and a field_map. Layer attributes are flattened into a Socrata-shaped
    payload (epoch-ms dates -> ISO date strings) so the Socrata normalizer is reused.
    Official open API, so robots is not consulted; per-domain rate limiting and the
    local cache still apply.
    """

    def __init__(self, source_name: str, config: SourceConfig, client: PoliteHttpClient):
        self.name = source_name
        self.config = config
        self.client = client
        self.latest_watermark: str | None = None

    def fetch(self, since: str | None = None, limit: int | None = None) -> Iterable[RawRecord]:
        if not self.config.arcgis_service_url:
            raise ValueError(f"{self.name}: arcgis_service_url is required")

        field_map = self.config.field_map
        date_field = self.config.date_field
        date_fields = list(field_map.project_date)
        id_field = field_map.record_id
        permit_field = field_map.permit_number

        page_size = min(self.config.page_size, ARCGIS_MAX_PAGE)
        if limit:
            page_size = min(page_size, limit)
        query_url = f"{self.config.arcgis_service_url.rstrip('/')}/{self.config.arcgis_layer}/query"
        where = f"{date_field} >= DATE '{_arcgis_date(since)}'" if since else "1=1"

        offset = 0
        fetched = 0
        max_seen: str | None = None
        while True:
            params: dict[str, object] = {
                "where": where,
                "outFields": "*",
                "orderByFields": "OBJECTID ASC",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "returnGeometry": "false",
                "f": "json",
            }
            data = self.client.get_json(query_url, params=params, check_robots=False)
            if not isinstance(data, dict):
                raise ValueError(f"Unexpected ArcGIS response for {self.name}: {data!r}")
            features = data.get("features") or []
            if not features:
                break

            for feature in features:
                payload = dict(feature.get("attributes") or {})
                for name in date_fields:
                    if isinstance(payload.get(name), (int, float)):
                        payload[name] = _epoch_ms_to_date(payload[name])
                payload["_source_url"] = self.source_url(payload)
                permit_number = str(payload.get(permit_field) or "") if permit_field else ""
                source_record_id = str(payload.get(id_field) or permit_number or stable_hash(payload))
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    payload=payload,
                    content_hash=stable_hash(payload),
                )
                fetched += 1
                date_value = payload.get(date_field)
                if isinstance(date_value, str) and (max_seen is None or date_value > max_seen):
                    max_seen = date_value
                if limit and fetched >= limit:
                    self.latest_watermark = max_seen
                    return

            offset += len(features)
            if not data.get("exceededTransferLimit") and len(features) < page_size:
                break

        self.latest_watermark = max_seen

    def source_url(self, payload: dict[str, object]) -> str | None:
        if not self.config.source_url_template:
            return None
        try:
            return self.config.source_url_template.format(**payload)
        except (KeyError, IndexError):
            return None


def _arcgis_date(value: str) -> str:
    return value.split("T", 1)[0]


def _epoch_ms_to_date(value: float) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()
