from __future__ import annotations

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.db import Database
from bay_area_projectintel.enrichment.browser import BrowserEnricher
from bay_area_projectintel.enrichment.cslb import CslbEnricher
from bay_area_projectintel.enrichment.web import PublicWebEnricher


class EnrichmentPipeline:
    def __init__(
        self,
        db: Database,
        client: PoliteHttpClient,
        cslb_master_csv,
        enable_browser: bool = False,
        target_licenses=None,
        settings=None,
    ):
        self.db = db
        self.cslb = CslbEnricher(cslb_master_csv, target_licenses=target_licenses)
        if settings is not None:
            self.web = PublicWebEnricher(
                client,
                max_discovery_candidates=settings.web_max_discovery_candidates,
                max_contact_links=settings.web_max_contact_links,
                min_discovery_token_len=settings.web_min_discovery_token_len,
            )
            self.browser = (
                BrowserEnricher(client, max_pages=settings.browser_max_pages) if enable_browser else None
            )
        else:
            self.web = PublicWebEnricher(client)
            self.browser = BrowserEnricher(client) if enable_browser else None

    def run(self, category: str | None = None, limit: int | None = None) -> dict[str, int]:
        stats = {"checked": 0, "updated": 0, "pending": 0, "skipped": 0}
        for row in self.db.get_projects_for_enrichment(category=category, limit=limit):
            stats["checked"] += 1
            company_id = row["company_id"]
            if company_id is None:
                self.db.record_enrichment_attempt(row["id"], "enrichment", "skipped", "No company candidate")
                stats["skipped"] += 1
                continue

            results = [
                self.cslb.enrich(row["company_name"], row["license_number"]),
                self.web.enrich(row["company_name"], row["website"]),
            ]
            if self.browser:
                results.append(self.browser.enrich(row["company_name"], row["website"]))

            updated = False
            for result in results:
                self.db.record_enrichment_attempt(row["id"], result.provider, result.status, result.detail)
                if result.status == "updated" and (result.email or result.phone or result.website):
                    self.db.update_company_contact(company_id, result.email, result.phone, result.website)
                    updated = True
                if result.address or result.license_status or result.license_classification:
                    self.db.update_company_license_info(
                        company_id, result.address, result.license_status, result.license_classification
                    )
            if updated:
                stats["updated"] += 1
            else:
                stats["pending"] += 1
        return stats
