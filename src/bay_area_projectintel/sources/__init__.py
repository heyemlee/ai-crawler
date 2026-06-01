from __future__ import annotations

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.config import RuntimeSettings, SourceConfig
from bay_area_projectintel.sources.arcgis import ArcGisPermitsSource
from bay_area_projectintel.sources.base import BaseSource
from bay_area_projectintel.sources.samgov import SamGovOpportunitiesSource
from bay_area_projectintel.sources.socrata import SocrataPermitsSource


def build_source(
    source_name: str,
    config: SourceConfig,
    client: PoliteHttpClient,
    settings: RuntimeSettings | None = None,
) -> BaseSource:
    if config.type == "socrata_building_permits":
        return SocrataPermitsSource(source_name, config, client)
    if config.type == "arcgis_building_permits":
        return ArcGisPermitsSource(source_name, config, client)
    if config.type == "samgov_opportunities":
        api_key = settings.sam_api_key if settings else None
        return SamGovOpportunitiesSource(source_name, config, client, api_key=api_key)
    raise ValueError(f"Unsupported source type: {config.type}")
