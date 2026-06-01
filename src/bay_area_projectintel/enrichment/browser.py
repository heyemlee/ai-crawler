from __future__ import annotations

from typing import Callable

from scrapling import Selector

from bay_area_projectintel.compliance.politeness import PoliteHttpClient
from bay_area_projectintel.enrichment.base import EnrichmentResult
from bay_area_projectintel.enrichment.web import (
    PublicWebEnricher,
    _contact_links,
    _email_from,
    _phone_from,
    candidate_urls,
    normalize_website,
)

FetchPage = Callable[[str], Selector]

MAX_BROWSER_PAGES = 4
INSTALL_HINT = "Browser kernel not available; run: pip install 'scrapling[fetchers]' && scrapling install"


class BrowserEnricher:
    """Opt-in enricher that renders JS-heavy contact pages with a real browser.

    Fetching goes through Scrapling's DynamicFetcher (Playwright). Robots.txt and
    rate limiting are still enforced via PoliteHttpClient. We never bypass logins,
    paywalls, or CAPTCHAs — pages that require those are simply left pending.
    """

    provider = "browser"

    def __init__(self, client: PoliteHttpClient, fetch_page: FetchPage | None = None, max_pages: int = MAX_BROWSER_PAGES):
        self.client = client
        self._fetch_page = fetch_page
        self.max_pages = max_pages

    def enrich(self, company_name: str | None, website: str | None = None) -> EnrichmentResult:
        fetch = self._fetch_page or _default_fetcher()
        if fetch is None:
            return EnrichmentResult(self.provider, "skipped", detail=INSTALL_HINT)

        target = normalize_website(website)
        if not target:
            target = PublicWebEnricher(self.client).discover_website(company_name)
        if not target:
            return EnrichmentResult(self.provider, "skipped", detail="No website to render")

        errors: list[str] = []
        queue = candidate_urls(target)
        seen: set[str] = set()
        rendered = 0
        index = 0
        while index < len(queue) and rendered < self.max_pages:
            url = queue[index]
            index += 1
            if url in seen:
                continue
            seen.add(url)
            page = self._render(fetch, url, errors)
            if page is None:
                continue
            rendered += 1
            if url == target:
                for linked in _contact_links(page, target):
                    if linked not in seen:
                        queue.append(linked)
            email = _email_from(page)
            phone = _phone_from(page)
            if email or phone:
                return EnrichmentResult(
                    self.provider,
                    "updated",
                    email=email,
                    phone=phone,
                    website=target,
                    detail=f"Rendered public contact on {url}",
                )

        detail = "No public email or phone after rendering"
        if errors:
            detail = f"{detail} ({'; '.join(errors[:2])})"
        return EnrichmentResult(self.provider, "not_found", detail=detail)

    def _render(self, fetch: FetchPage, url: str, errors: list[str]) -> Selector | None:
        try:
            self.client.ensure_allowed(url)
            self.client.throttle(url)
            return fetch(url)
        except PermissionError:
            errors.append(f"{url}: blocked by robots")
            return None
        except Exception as exc:  # browser/runtime failures should not abort the run
            errors.append(f"{url}: {exc.__class__.__name__}")
            return None


def _default_fetcher() -> FetchPage | None:
    try:
        from scrapling.fetchers import DynamicFetcher
    except ImportError:
        return None

    def fetch(url: str) -> Selector:
        return DynamicFetcher.fetch(url, headless=True, network_idle=True)

    return fetch
