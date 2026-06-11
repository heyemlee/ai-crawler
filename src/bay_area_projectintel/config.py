from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path.cwd()


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROJECTINTEL_", extra="ignore")

    db_path: Path = Path("data/projectintel.sqlite3")
    cache_dir: Path = Path(".cache/projectintel")
    cslb_master_csv: Path = Path(".cache/projectintel/cslb/MasterLicenseData.csv")
    user_agent: str = "BayAreaProjectIntel/0.1 (contact: you@example.com)"
    default_lookback_days: int = 90
    # Stable path to the most recent export, so a scheduler / WeChat bridge can always
    # return "the latest Excel" without knowing the per-run filename.
    latest_excel_path: Path = Path("data/latest-leads.xlsx")
    # Where `notify` appends summaries when run unattended (e.g. by launchd).
    notify_log_path: Path = Path("data/notify.log")

    # --- Tunables (override via PROJECTINTEL_* env or .env) ---
    # Compliance: minimum seconds between requests to the same domain.
    politeness_min_interval: float = 0.35
    # Cross-source dedupe: rapidfuzz token_set_ratio cutoffs (0-100). Conservative
    # by design — over-merging loses a distinct lead, which is worse than a stray dup.
    dedupe_address_threshold: int = 92
    dedupe_title_threshold: int = 72
    # Public-web enrichment: how aggressively to discover/crawl a company site.
    web_max_discovery_candidates: int = 6
    web_max_contact_links: int = 4
    web_min_discovery_token_len: int = 4
    # Browser enrichment: max pages rendered per company (opt-in, expensive).
    browser_max_pages: int = 4

    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", validation_alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    sam_api_key: str | None = Field(default=None, validation_alias="SAM_API_KEY")


class SocrataFieldMap(BaseModel):
    """Maps logical project fields to a Socrata dataset's column names.

    Defaults follow the DataSF Building Permits schema; other cities override the
    fields whose column names differ. Lists are tried in order (first non-empty wins
    for single-value fields; address parts are concatenated in order).
    """

    record_id: str = "record_id"
    permit_number: str | None = "permit_number"
    description: list[str] = Field(default_factory=lambda: ["description", "permit_type_definition"])
    address: list[str] = Field(
        default_factory=lambda: ["street_number", "street_number_suffix", "street_name", "street_suffix", "unit"]
    )
    project_date: list[str] = Field(
        default_factory=lambda: ["issued_date", "filed_date", "permit_creation_date", "data_loaded_at"]
    )
    # County-level datasets span many cities; map the per-record city column here.
    # None falls back to the source's jurisdiction.
    city: str | None = None
    # When a city has no separate contacts dataset, the contractor may live on the
    # permit record itself. None means "not available on the main record".
    company_name: str | None = None
    company_license: str | None = None
    company_email: str | None = None
    company_phone: str | None = None


class SourceConfig(BaseModel):
    type: str
    name: str
    jurisdiction: str
    county: str
    access: str = "api"
    page_size: int = 500
    source_url_template: str | None = None

    # Socrata
    domain: str | None = None
    dataset_id: str | None = None
    contacts_dataset_id: str | None = None
    date_field: str = "data_loaded_at"
    field_map: SocrataFieldMap = Field(default_factory=SocrataFieldMap)

    # ArcGIS FeatureServer (config-driven, shares field_map). The layer's attributes
    # are flattened to a Socrata-shaped payload so normalize is reused; epoch-ms date
    # fields named in field_map.project_date are converted to ISO dates by the adapter.
    arcgis_service_url: str | None = None
    arcgis_layer: int = 0

    # Accela / EnerGov / other permit portals that expose a public JSON search
    # endpoint. These adapters normalize the returned records into the same
    # payload shape as Socrata so the existing normalize/classify/export pipeline
    # is reused.
    search_url: str | None = None
    detail_url_template: str | None = None
    record_path: str | None = None
    query_params: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    criteria_url: str | None = None
    since_param: str | None = None
    limit_param: str | None = None
    offset_param: str | None = None
    page_param: str | None = None

    # SAM.gov opportunities
    api_base_url: str = "https://api.sam.gov/opportunities/v2/search"
    naics_codes: list[str] = Field(default_factory=list)
    ptype: str | None = None
    # Client-side place-of-performance filter: "bay_area" (nine counties) or a
    # two-letter state code like "CA". None disables geographic filtering.
    region: str | None = None
    # Cap pages per NAICS code so heavy client-side filtering does not page deep
    # and trip the free SAM key's rate limit.
    max_pages: int = 5


class AppConfig(BaseModel):
    settings: RuntimeSettings
    sources: dict[str, SourceConfig]
    jurisdictions: list[dict[str, Any]]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(root: Path | None = None) -> AppConfig:
    root = root or ROOT
    load_dotenv(root / ".env")

    settings = RuntimeSettings()
    sources_doc = _read_yaml(root / "config" / "sources.yaml")
    jurisdictions_doc = _read_yaml(root / "config" / "jurisdictions.yaml")

    sources = {
        name: SourceConfig.model_validate(value)
        for name, value in (sources_doc.get("sources") or {}).items()
    }

    return AppConfig(
        settings=settings,
        sources=sources,
        jurisdictions=jurisdictions_doc.get("jurisdictions") or [],
    )
