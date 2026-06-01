from bay_area_projectintel.enrichment.web import (
    PublicWebEnricher,
    candidate_urls,
    company_slug_variants,
    contact_links,
    extract_email,
    extract_phone,
    normalize_website,
    website_candidates,
    website_matches_company,
)


class FakeClient:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        self.requested.append(url)
        if url not in self.pages:
            raise OSError("not found")
        return self.pages[url]


def test_extract_email_and_phone_from_html() -> None:
    html = """
    <html>
      <a href="mailto:info@acmebuilders.com">Email us</a>
      <a href="tel:14155551212">Call</a>
    </html>
    """

    assert extract_email(html) == "info@acmebuilders.com"
    assert extract_phone(html) == "(415) 555-1212"


def test_extract_email_from_mailto_href_with_query_and_encoding() -> None:
    html = '<a href="MAILTO:Sales%40acmebuilders.com?subject=Hi,other@x.com">Reach us</a>'

    assert extract_email(html) == "Sales@acmebuilders.com"


def test_extract_phone_from_tel_href_with_formatting() -> None:
    html = '<a href="tel:+1%20(415)%20555-1212">Call</a>'

    assert extract_phone(html) == "(415) 555-1212"


def test_extract_email_ignores_script_and_style_content() -> None:
    html = """
    <html>
      <head><style>.a{content:"fake@asset.com"}</style></head>
      <body>
        <script>var leak = "bot@tracker.com";</script>
        <p>Real contact: hello@acmebuilders.com</p>
      </body>
    </html>
    """

    assert extract_email(html) == "hello@acmebuilders.com"


def test_contact_links_uses_css_and_filters_external_and_noise() -> None:
    html = """
    <DIV>
      <A HREF="/Contact-Us/">Contact</A>
      <a href="about?ref=nav">About</a>
      <a href="https://twitter.com/acme">Social</a>
      <a href="/pricing">Pricing</a>
      <a href="mailto:info@acmebuilders.com">Email</a>
    </DIV>
    """

    links = contact_links(html, "https://acmebuilders.com")

    assert links == [
        "https://acmebuilders.com/Contact-Us",
        "https://acmebuilders.com/about",
    ]


def test_normalize_website_adds_https_and_strips_noise() -> None:
    assert normalize_website("AcmeBuilders.com/contact?utm=x#top") == "https://acmebuilders.com/contact"
    assert normalize_website("") is None


def test_candidate_urls_include_common_contact_pages() -> None:
    assert candidate_urls("https://acmebuilders.com")[:3] == [
        "https://acmebuilders.com",
        "https://acmebuilders.com/contact",
        "https://acmebuilders.com/contact-us",
    ]


def test_website_candidates_use_conservative_company_slug_variants() -> None:
    assert company_slug_variants("Acme Builders Inc.") == ["acmebuilders", "acme-builders"]
    assert website_candidates("Acme Builders Inc.")[:2] == [
        "https://acmebuilders.com",
        "https://www.acmebuilders.com",
    ]


def test_website_candidates_skips_short_acronym_names() -> None:
    assert website_candidates("Gci, Inc") == []
    assert website_candidates("GCI") == []
    assert website_candidates("AECOM")[:1] == ["https://aecom.com"]


def test_website_matches_company_requires_domain_or_page_text_match() -> None:
    assert website_matches_company("Acme Builders Inc.", "<title>Acme</title>", "https://acmebuilders.com")
    assert website_matches_company("Acme Builders Inc.", "<title>Acme Builders</title>", "https://example.com")
    assert not website_matches_company("Acme Builders Inc.", "<title>Welcome</title>", "https://acmebuilders.com")
    assert not website_matches_company("Acme Builders Inc.", "<title>Other Contractor</title>", "https://example.com")


def test_public_web_enricher_fetches_homepage_and_contact_link() -> None:
    client = FakeClient(
        {
            "https://acmebuilders.com": '<a href="/contact-us">Contact</a>',
            "https://acmebuilders.com/contact-us": "Reach us at info@acmebuilders.com or 415-555-1212",
        }
    )

    result = PublicWebEnricher(client).enrich_known_website("acmebuilders.com")

    assert result.status == "updated"
    assert result.website == "https://acmebuilders.com"
    assert result.email == "info@acmebuilders.com"
    assert result.phone == "(415) 555-1212"
    assert "https://acmebuilders.com/contact-us" in client.requested


def test_public_web_enricher_discovers_matching_company_website() -> None:
    client = FakeClient(
        {
            "https://acmebuilders.com": '<title>Acme Builders</title><a href="/contact">Contact</a>',
            "https://acmebuilders.com/contact": "Reach us at info@acmebuilders.com",
        }
    )

    result = PublicWebEnricher(client).enrich("Acme Builders Inc.", None)

    assert result.status == "updated"
    assert result.website == "https://acmebuilders.com"
    assert result.email == "info@acmebuilders.com"
