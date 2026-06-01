from scrapling import Selector

from bay_area_projectintel.enrichment.browser import BrowserEnricher


class FakeClient:
    """Stands in for PoliteHttpClient: records robots/throttle calls."""

    def __init__(self, pages: dict[str, str] | None = None, blocked: set[str] | None = None):
        self.pages = pages or {}
        self.blocked = blocked or set()
        self.allowed_checks: list[str] = []
        self.throttled: list[str] = []

    def ensure_allowed(self, url: str) -> None:
        self.allowed_checks.append(url)
        if url in self.blocked:
            raise PermissionError(f"robots.txt disallows {url}")

    def throttle(self, url: str) -> None:
        self.throttled.append(url)

    def get_text(self, url: str, use_cache: bool = True) -> str:
        if url not in self.pages:
            raise OSError("not found")
        return self.pages[url]


def make_fetcher(rendered: dict[str, str]):
    requested: list[str] = []

    def fetch(url: str) -> Selector:
        requested.append(url)
        if url not in rendered:
            raise RuntimeError("render timeout")
        return Selector(content=rendered[url], url=url)

    fetch.requested = requested  # type: ignore[attr-defined]
    return fetch


def test_browser_skipped_when_kernel_unavailable() -> None:
    enricher = BrowserEnricher(FakeClient(), fetch_page=None)
    # No real browser kernel is installed in CI; default fetcher resolves to None.
    result = enricher.enrich("Acme Builders Inc.", "https://acmebuilders.com")
    assert result.status == "skipped"
    assert "scrapling[fetchers]" in (result.detail or "")


def test_browser_extracts_contact_from_js_rendered_page() -> None:
    client = FakeClient()
    fetch = make_fetcher(
        {
            "https://acmebuilders.com": '<a href="/contact-us">Contact</a>',
            "https://acmebuilders.com/contact-us": '<a href="mailto:info@acmebuilders.com">Email</a>'
            '<a href="tel:14155551212">Call</a>',
        }
    )
    enricher = BrowserEnricher(client, fetch_page=fetch)

    result = enricher.enrich("Acme Builders Inc.", "https://acmebuilders.com")

    assert result.status == "updated"
    assert result.email == "info@acmebuilders.com"
    assert result.phone == "(415) 555-1212"
    assert result.website == "https://acmebuilders.com"
    # Robots check + throttle ran before every render.
    assert "https://acmebuilders.com/contact-us" in client.allowed_checks
    assert "https://acmebuilders.com/contact-us" in client.throttled


def test_browser_respects_robots_block() -> None:
    blocked = {"https://acmebuilders.com/contact"}
    client = FakeClient(blocked=blocked)
    fetch = make_fetcher(
        {
            "https://acmebuilders.com": "<p>nothing here</p>",
            "https://acmebuilders.com/contact": "info@acmebuilders.com",
        }
    )
    enricher = BrowserEnricher(client, fetch_page=fetch)

    result = enricher.enrich("Acme Builders Inc.", "https://acmebuilders.com")

    assert result.status == "not_found"
    assert "https://acmebuilders.com/contact" not in fetch.requested  # type: ignore[attr-defined]


def test_browser_skips_when_no_website_and_no_discovery() -> None:
    enricher = BrowserEnricher(FakeClient(), fetch_page=make_fetcher({}))
    result = enricher.enrich(None, None)
    assert result.status == "skipped"
    assert "No website" in (result.detail or "")
